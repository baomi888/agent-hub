"""安全邮箱设置业务；身份和版本从账号库读取，不接受客户端快照。"""

import json
import secrets
import time

from core import mailer, security_mail
from core.verification import Context, VerificationError, normalize_address


class SecurityEmailError(VerificationError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class SecurityEmailService:
    def __init__(self, store, *, sender=security_mail.send_code, ready=None):
        self.store = store
        self.sender = sender
        self.ready = ready or (lambda: security_mail.status()["ready"])

    def _context(self, db, uid, purpose, address, flow):
        user = db.execute("SELECT status,token_valid_after FROM users WHERE id=?", (uid,)).fetchone()
        if not user or user["status"] != 1:
            raise SecurityEmailError("账号不可用", 401)
        bound = db.execute("SELECT * FROM security_emails WHERE user_id=?", (uid,)).fetchone()
        if purpose == "bind_email":
            if bound:
                raise SecurityEmailError("已绑定安全邮箱，请使用换绑", 409)
        elif purpose in ("change_email_old", "change_email_new"):
            if not bound:
                raise SecurityEmailError("请先绑定安全邮箱")
            if purpose == "change_email_old":
                address = bound["address"]
        else:
            raise SecurityEmailError("此验证用途尚未开放")
        address = normalize_address(address)
        if purpose != "change_email_old":
            other = db.execute("SELECT user_id FROM security_emails WHERE address=?", (address,)).fetchone()
            if other:
                raise SecurityEmailError("邮箱已绑定，请使用其他邮箱", 409)
        return Context(purpose, address, uid, flow, user["token_valid_after"], bound["binding_version"] if bound else 0)

    def state(self, uid):
        with self.store.transaction() as db:
            user = db.execute("SELECT status FROM users WHERE id=?", (uid,)).fetchone()
            if not user or user["status"] != 1:
                raise SecurityEmailError("账号不可用", 401)
            row = db.execute("SELECT address FROM security_emails WHERE user_id=?", (uid,)).fetchone()
        return {
            "ready": bool(self.ready()),
            "bound": bool(row),
            "masked_email": mailer._mask(row["address"]) if row else None,
        }

    def send(self, uid, purpose, address, flow, ip):
        if not self.ready():
            raise SecurityEmailError("安全邮件服务暂不可用", 503)
        flow = flow or secrets.token_urlsafe(24)
        with self.store.transaction() as db:
            context = self._context(db, uid, purpose, address or "", flow)
        request_id = self.store.send(context, ip, self.sender)
        return {
            "request_id": request_id,
            "flow": flow,
            "expires_in": security_mail.CODE_TTL_SECONDS,
            "resend_after": self.store.limits.cooldown,
        }

    def verify(self, uid, request_id, code):
        with self.store.transaction() as db:
            row = db.execute("SELECT context FROM verification_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise SecurityEmailError("验证请求不存在或已失效")
            expected = Context(**json.loads(row["context"]))
            if expected.subject != uid:
                raise SecurityEmailError("验证请求不存在或已失效")
            current = self._context(db, uid, expected.purpose, expected.address, expected.flow)
        return {"grant": self.store.verify(request_id, code, current)}

    def _proof(self, uid, grant, purpose):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT context FROM verification_grants WHERE digest=?", (self.store._hash("grant", grant),)
            ).fetchone()
        if not row:
            raise SecurityEmailError("验证凭证不存在或已失效")
        context = Context(**json.loads(row["context"]))
        if context.subject != uid or context.purpose != purpose:
            raise SecurityEmailError("验证凭证不存在或已失效")
        return context

    def _current(self, db, expected):
        return self._context(db, expected.subject, expected.purpose, expected.address, expected.flow)

    def bind(self, uid, grant):
        context = self._proof(uid, grant, "bind_email")

        def effect(db):
            db.execute("INSERT INTO security_emails VALUES (?,?,?,1)", (uid, context.address, int(self.store.clock())))

        self.store.consume(grant, context, effect, current_context=self._current)
        return self.state(uid)

    def change(self, uid, old_grant, new_grant):
        old = self._proof(uid, old_grant, "change_email_old")
        new = self._proof(uid, new_grant, "change_email_new")

        def effect(db):
            db.execute(
                "UPDATE security_emails SET address=?,verified_at=?,binding_version=binding_version+1 WHERE user_id=?",
                (new.address, int(self.store.clock()), uid),
            )

        self.store.consume_many([(old_grant, old), (new_grant, new)], effect, current_context=self._current)
        return self.state(uid)

    def send_signup(self, nonce, address, ip):
        if not self.ready():
            raise SecurityEmailError("注册邮箱验证暂不可用", 503)
        context = Context("register_email", normalize_address(address), "signup:" + nonce, nonce)
        request_id = self.store.send(context, ip, self.sender)
        return {"request_id": request_id, "expires_in": 600, "resend_after": self.store.limits.cooldown}

    def verify_signup(self, nonce, request_id, code):
        with self.store.transaction() as db:
            row = db.execute("SELECT context FROM verification_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise SecurityEmailError("验证请求不存在或已失效")
        context = Context(**json.loads(row["context"]))
        if context.purpose != "register_email" or context.subject != "signup:" + nonce or context.flow != nonce:
            raise SecurityEmailError("验证请求不存在或已失效")
        return {"grant": self.store.verify(request_id, code, context)}

    def register_signup(self, nonce, username, password, address, grant):
        import uuid

        from core import account
        from core.auth import hash_password

        context = self._proof("signup:" + nonce, grant, "register_email")
        if context.flow != nonce or context.address != normalize_address(address):
            raise SecurityEmailError("邮箱验证信息已变化，请重新验证")
        uid, now, password_hash = uuid.uuid4().hex, int(self.store.clock()), hash_password(password)
        # 归一化只做一次：下面查重和 INSERT 必须用同一个值，
        # 否则「苞米」和「 苞米 」会查重漏过、INSERT 撞唯一约束
        normalized_username = account.normalize_name(username)

        def effect(db):
            # ★ 2026-11-23 修「用已存在的用户名注册，却报邮箱已被绑定」：
            #   users.username 有唯一约束，撞了抛 IntegrityError，
            #   而 api/security_email.py 把所有 IntegrityError 都翻译成邮箱冲突，
            #   用户看到的是一句和实际原因无关的话。
            #   这里在写库之前显式查一次，给一句说得通的。
            #   放在 effect 内（而非更外层）是关键：异常会让 consume() 的事务整体回滚，
            #   验证码不会被白白吃掉 —— 用户换个名字还能用同一张凭证重试。
            if db.execute(
                "SELECT 1 FROM users WHERE username=?", (normalized_username,)
            ).fetchone():
                raise SecurityEmailError("该登录名已被占用", 409)
            db.execute(
                "INSERT INTO users(id,username,password_hash,created_at,last_login_at) VALUES (?,?,?,?,?)",
                (uid, normalized_username, password_hash, now, now),
            )
            db.execute("INSERT INTO security_emails VALUES (?,?,?,1)", (uid, context.address, now))

        self.store.consume(grant, context, effect)
        return account.get_user(uid)

    def _reset_context(self, db, uid, flow):
        user = db.execute(
            "SELECT * FROM users WHERE id=? AND status=1",
            (uid,),
        ).fetchone()
        email = db.execute("SELECT * FROM security_emails WHERE user_id=?", (uid,)).fetchone()
        if not user or not email:
            raise SecurityEmailError("验证请求不存在或已失效")
        return Context(
            "reset_password", email["address"], uid, flow, user["token_valid_after"], email["binding_version"]
        )

    # 找回密码「已注册 / 未注册」两条路径的最小响应耗时。
    # 已注册会真的发信（慢），未注册直接返回（快）—— 响应时间本身就是
    # 一个「这个邮箱有没有账号」的探针，文案归一了也没用（2026-11-23）。
    RESET_MIN_SECONDS = 0.15

    def send_reset(self, nonce, address, ip):
        """找回密码验证码。★ 两条分支共用同一个最低耗时。

        包一层而不是把 sleep 写进某支里：写进「已注册」那支等于没加，
        写进「未注册」那支也一样 —— 必须罩住整个函数的所有出口，
        包括 ready() 抛 503 的那条。用 try/finally 保证异常路径也补时。
        """
        started = time.monotonic()
        try:
            return self._send_reset(nonce, address, ip)
        finally:
            remaining = self.RESET_MIN_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

    def _send_reset(self, nonce, address, ip):
        if not self.ready():
            raise SecurityEmailError("邮件服务暂不可用，请稍后重试", 503)
        address = normalize_address(address)
        with self.store.transaction() as db:
            row = db.execute("SELECT user_id FROM security_emails WHERE address=?", (address,)).fetchone()
            try:
                context = self._reset_context(db, row["user_id"], nonce) if row else None
            except SecurityEmailError:
                context = None
        exists = context is not None
        if not exists:
            context = Context("reset_password", address, "unknown:" + self.store._hash("unknown", address), nonce)
        # ★ charge_address=exists —— 见 core/verification.py reserve 的注释：
        #   未注册地址不占地址维度的日限额与冷却，否则任何人拿一个不存在的地址
        #   连发 10 次，就能把这个地址的真实主人当天彻底锁死（2026-11-23）。
        #   已注册地址照旧全额计费，找回密码的限流强度不变。
        reservation = self.store.reserve(context, ip, charge_address=exists)
        accepted = False
        if exists:
            try:
                accepted = self.sender(address, reservation.code, "reset_password").get("ok") is True
            except Exception:
                accepted = False
        self.store.finish(reservation.request_id, accepted)
        # 不宣称投递成功；已注册、未注册、账号不可用及发送故障返回相同文案和字段。
        return {
            "request_id": reservation.request_id,
            "expires_in": 600,
            "resend_after": self.store.limits.cooldown,
            "message": "如果该邮箱已绑定可用账号，验证码将发送到该邮箱，请检查收件箱或稍后重试。",
        }

    def verify_reset(self, nonce, request_id, code):
        with self.store.transaction() as db:
            row = db.execute("SELECT context FROM verification_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise SecurityEmailError("验证请求不存在或已失效")
            expected = Context(**json.loads(row["context"]))
            if expected.purpose != "reset_password" or expected.flow != nonce:
                raise SecurityEmailError("验证请求不存在或已失效")
            current = self._reset_context(db, expected.subject, nonce)
        return {"grant": self.store.verify(request_id, code, current)}

    # ---------- 邮箱验证码登录 ----------

    def _login_context(self, db, uid, flow):
        """登录用途的上下文：只认"已绑定且账号可用"，其余一律按不存在处理。

        与 _reset_context 同构，但 purpose 不同 —— 证明互不通用，找回的验证码
        拿来登录会被 purpose 比对拦下，反之亦然。
        """
        user = db.execute(
            "SELECT * FROM users WHERE id=? AND status=1",
            (uid,),
        ).fetchone()
        email = db.execute("SELECT * FROM security_emails WHERE user_id=?", (uid,)).fetchone()
        if not user or not email:
            raise SecurityEmailError("验证请求不存在或已失效")
        return Context(
            "login_email", email["address"], uid, flow, user["token_valid_after"], email["binding_version"]
        )

    def send_login(self, nonce, address, ip):
        """匿名发码：只看"这个地址有没有绑在可用账号上"，不看调用者是谁。

        与 send_reset 相同的防枚举口径：已绑定、未绑定、账号停用、发送故障
        返回相同字段和文案，请求方无法从响应分辨邮箱是否注册过。
        """
        if not self.ready():
            raise SecurityEmailError("邮件服务暂不可用，请稍后重试", 503)
        address = normalize_address(address)
        with self.store.transaction() as db:
            row = db.execute("SELECT user_id FROM security_emails WHERE address=?", (address,)).fetchone()
            try:
                context = self._login_context(db, row["user_id"], nonce) if row else None
            except SecurityEmailError:
                context = None
        exists = context is not None
        if not exists:
            context = Context("login_email", address, "unknown:" + self.store._hash("unknown", address), nonce)
        reservation = self.store.reserve(context, ip)
        accepted = False
        if exists:
            try:
                accepted = self.sender(address, reservation.code, "login_email").get("ok") is True
            except Exception:
                accepted = False
        self.store.finish(reservation.request_id, accepted)
        return {
            "request_id": reservation.request_id,
            "expires_in": 600,
            "resend_after": self.store.limits.cooldown,
            "message": "如果该邮箱已绑定可用账号，验证码将发送到该邮箱，请检查收件箱或稍后重试。",
        }

    def login_by_email(self, nonce, request_id, code):
        """验证码换会话：验证通过即代表持有邮箱，等价于密码登录的一次成功。

        consume 的 current_context 会在消费瞬间重查绑定与账号状态 —— 邮箱
        刚被换绑或账号刚被停用时，旧的验证码不能再登录。
        """
        from core import account

        with self.store.transaction() as db:
            row = db.execute("SELECT context FROM verification_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise SecurityEmailError("验证请求不存在或已失效")
            expected = Context(**json.loads(row["context"]))
            if expected.purpose != "login_email" or expected.flow != nonce:
                raise SecurityEmailError("验证请求不存在或已失效")
            current = self._login_context(db, expected.subject, nonce)
        # verify 在事务外调：它内部自己开事务，嵌套会把 sqlite 锁死
        grant = self.store.verify(request_id, code, current)

        def current_context(db, context):
            return self._login_context(db, context.subject, context.flow)

        def effect(db):
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (int(self.store.clock()), expected.subject))

        self.store.consume(grant, current, effect, current_context=current_context)
        user = account.get_user(expected.subject)
        if not user or user.get("status") != 1:
            raise SecurityEmailError("验证请求不存在或已失效")
        return user

    def reset_password(self, nonce, grant, password, confirm):
        from core import account
        from core.auth import hash_password

        if password != confirm:
            raise SecurityEmailError("两次输入的新密码不一致")
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT context FROM verification_grants WHERE digest=?", (self.store._hash("grant", grant),)
            ).fetchone()
            if not row:
                raise SecurityEmailError("验证凭证不存在或已失效")
            expected = Context(**json.loads(row["context"]))
            if expected.purpose != "reset_password" or expected.flow != nonce:
                raise SecurityEmailError("验证凭证不存在或已失效")
            user = db.execute("SELECT username FROM users WHERE id=?", (expected.subject,)).fetchone()
            if not user:
                raise SecurityEmailError("验证凭证不存在或已失效")
            error = account.check_password(password, user["username"])
            if error:
                raise SecurityEmailError(error)
        password_hash = hash_password(password)

        def current(db, context):
            return self._reset_context(db, context.subject, context.flow)

        def effect(db):
            db.execute(
                "UPDATE users SET password_hash=?,token_valid_after=token_valid_after+1,"
                "failed_count=0,lock_until=0 WHERE id=?",
                (password_hash, expected.subject),
            )

        self.store.consume(grant, expected, effect, current_context=current)
        return {"ok": True}
