"""账号安全三项修复的守卫（2026-11-23，来源 HZL PR #30 重建）。

三件事，每件都要有「修好了」和「没修会红」两面：

1. 用已存在的用户名注册 → 必须回「该登录名已被占用」，而不是今天那句
   不相干的「邮箱已被绑定」；且查重必须在 INSERT 之前、在 effect 之内。
2. 对未注册地址连发找回码 → 不能让该地址的真实主人当天发不出信。
   同时已注册地址的限流强度必须一点没降（反向守卫）。
3. 找回码「已注册 / 未注册」两条路径要有共同的最低响应耗时。
"""

import os
import tempfile
import unittest

from core import security_email, verification

SEC_EMAIL = "core/security_email.py"


class _Clock:
    def __init__(self, now=1_700_000_000):
        self.now = now

    def __call__(self):
        return self.now


def _tmpdir():
    """建一个本次运行专用的临时目录。

    不用 unittest 的 tmp_path（那是 pytest fixture，TestCase 方法接不到）。
    Windows 上 SQLite 句柄释放有延迟，目录不主动删 —— 交给系统临时目录清理，
    删不掉反而会让测试假失败。
    """
    return tempfile.mkdtemp(prefix="accsec_")


def _store(d):
    # path / secret 是位置参数，clock 只能传关键字；表要先 migrate 出来
    store = verification.VerificationStore(os.path.join(d, "v.db"), b"x" * 32, clock=_Clock())
    store.migrate()
    return store


def _ctx(flow, address="victim@example.com", subject="unknown:abc"):
    return verification.Context("reset_password", address, subject, flow)


class TestReserveAddressDimension(unittest.TestCase):
    """core/verification.py: reserve(charge_address=...)。"""

    def test_unregistered_address_does_not_eat_daily_quota(self):
        """★ 核心断言：未注册地址连发，不该被地址日限额挡住。

        这正是把用户锁死的那条路：攻击者拿 victim@example.com 连发 10 次，
        今天 victim 本人当天就再也发不出找回邮件。
        """
        store = _store(_tmpdir())
        sent = 0
        for i in range(12):
            try:
                store.reserve(_ctx("flow-%d" % i), "1.1.1.1", charge_address=False)
                sent += 1
            except verification.VerificationError:
                pass
        self.assertGreater(
            sent, verification.Limits().daily,
            "未注册地址仍被地址日限额挡住 —— 锁死漏洞没修好",
        )

    def test_registered_address_still_charged(self):
        """★ 反向守卫：charge_address=True 时地址维度必须照旧生效。

        别为了修上面那条，把真正的限流一起放开了。
        """
        store = _store(_tmpdir())
        daily = store.limits.daily
        sent = 0
        for i in range(daily + 4):
            try:
                store.reserve(
                    _ctx("flow-%d" % i, address="real@example.com", subject="uid-1"),
                    "2.2.2.2",
                    charge_address=True,
                )
                sent += 1
            except verification.VerificationError:
                pass
        self.assertLessEqual(sent, daily, "已注册地址的日限额被放开了 —— 限流变弱")

    def test_default_keeps_old_behaviour(self):
        """★ 不传参数时行为必须完全等于修改前（其它调用方不受影响）。"""
        store = _store(_tmpdir())
        ctx = _ctx("f", address="a@b.com", subject="s")
        store.reserve(ctx, "3.3.3.3")
        # 冷却立刻生效 —— 这正是修改前的行为
        with self.assertRaises(verification.VerificationError):
            store.reserve(ctx, "3.3.3.3")


class TestResetTimingAndConflictText(unittest.TestCase):
    def test_reset_min_duration_constant(self):
        self.assertTrue(
            hasattr(security_email.SecurityEmailService, "RESET_MIN_SECONDS"),
            "缺少固定耗时，账号存在性仍可通过响应时间探知",
        )
        self.assertGreaterEqual(
            security_email.SecurityEmailService.RESET_MIN_SECONDS, 0.1
        )

    def test_send_reset_is_wrapped_for_timing(self):
        """send_reset 必须是「包一层再调 _send_reset」。

        如果把 sleep 写进任何一个分支里，另一条分支就没有延迟，等于没加 ——
        所以要求这个「外层罩住所有出口」的形状，而不是只检查有没有 sleep。
        """
        src = open(SEC_EMAIL, encoding="utf-8").read()
        self.assertIn("def _send_reset(", src, "缺少被包裹的内层实现")
        outer = src.split("def send_reset(", 1)[1].split("def _send_reset(", 1)[0]
        self.assertIn("finally:", outer, "外层没用 try/finally，异常路径不会补时")
        self.assertIn("time.sleep", outer, "外层没有 sleep，固定耗时没生效")

    def test_username_conflict_checked_before_insert(self):
        """查重必须在 INSERT 之前、且在 effect 内（异常才能回滚掉验证码）。"""
        src = open(SEC_EMAIL, encoding="utf-8").read()
        self.assertIn("该登录名已被占用", src)
        dup = src.index("该登录名已被占用")
        ins = src.index("INSERT INTO users(")
        self.assertLess(dup, ins, "查重写在了 INSERT 之后 —— 撞约束时提示还是错的")
        effect_at = src.index("def effect(db):")
        self.assertGreater(dup, effect_at, "查重不在 effect 内，验证码会被白吃")


class TestEnvExampleWording(unittest.TestCase):
    def test_env_example_does_not_mislead(self):
        """默认配置下注册整体不可用，注释不能让人以为「只是不验邮箱」。"""
        text = open(".env.example", encoding="utf-8").read()
        self.assertIn("自助注册整体关闭", text)
        self.assertIn("503", text)
        self.assertNotIn("前端注册不要求邮箱验证", text)


if __name__ == "__main__":
    unittest.main()