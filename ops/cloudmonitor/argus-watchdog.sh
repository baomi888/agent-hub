#!/bin/bash
# 云监控 argusagent 看门狗
#
# 要解决的故障模式（2026-11-14 实际发生）：
#   1. 采集子进程每 15 秒自检一次 cpu/mem/fd/io，任一项连续超限就 "will exit" 自杀；
#   2. 每次自杀把 <ts>|resourceCount|N 累加进 agent.status，累到 agent.status.resource.count(4)
#      父进程就永久拒绝拉起 —— 日志只打印 "disabled because of resourceCount exceed ..."；
#   3. systemctl restart 不清零这个计数，所以看门狗必须先 stop、改写计数、再 start。
#
# 判定四态：service 未 active / 父进程没了 / 父进程在但子进程没了 / 上报文件过期。
# 健康时只写一行 OK 日志，不碰服务。
set -u

BASE=/usr/local/cloudmonitor
STATUS_FILE=$BASE/agent.status
STATUS_BAK=$BASE/agent.status.watchdog.bak
LAST_SEND=$BASE/local_data/logs/argus-last-send-cms.txt
AGENT_LOG=$BASE/local_data/logs/argusagent.log
LOG=/var/log/argus-watchdog.log
STAMP=/var/run/argus-watchdog.last

COOLDOWN=${COOLDOWN:-600}   # 两次动作最小间隔，避免真故障时每 5 分钟反复打死服务
STALE=${STALE:-900}         # 上报文件超过这么久没更新，视为假活
LOG_MAX=2097152

log() { printf '%s [%s] %s\n' "$(date '+%F %T')" "$1" "$2" >> "$LOG"; }

# 日志自限，不留成第二个隐患
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt "$LOG_MAX" ]; then
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

now=$(date +%s)

# ---- 维护期暂停开关：手动停服务做维护前，touch /etc/argus-watchdog.disabled ----
if [ -f /etc/argus-watchdog.disabled ]; then
    log SKIP "paused by /etc/argus-watchdog.disabled"
    exit 0
fi

# ---- 取状态 ----
active=$(systemctl is-active cloudmonitor 2>/dev/null || true)
# 必须锚定行首 ^：否则 shell 包装层（如 bash -c "…bin/argusagent -d…"）的 cmdline
# 会含同样的字符串被一起数进来，父/子进程数双双虚高，判定全部失真。
parent=$(pgrep -fc "^$BASE/bin/argusagent -d" 2>/dev/null || true)
child=$(pgrep -fc "^$BASE/bin/argusagent$" 2>/dev/null || true)
parent=${parent:-0}
child=${child:-0}

reason=""
if [ "$active" != "active" ]; then
    reason="service_${active:-unknown}"
elif [ "$parent" -eq 0 ]; then
    reason="parent_missing"
elif [ "$child" -eq 0 ]; then
    # 关键判据：父进程在、子进程没了 = 被 resourceCount 闸住的典型形态
    reason="child_missing"
elif [ -f "$LAST_SEND" ]; then
    age=$((now - $(stat -c %Y "$LAST_SEND")))
    if [ "$age" -gt "$STALE" ]; then
        reason="report_stale_${age}s"
    fi
fi

if [ -z "$reason" ]; then
    log OK "healthy active=$active parent=$parent child=$child"
    exit 0
fi

# ---- 冷却：只拦"动手"，不拦"看一眼" ----
# 放在健康判定之后是有意的：冷却期内也要每 5 分钟留下一条 OK 心跳，
# 否则日志里连续几条 SKIP 会让人误判"看门狗根本没在跑"。
last=0
[ -f "$STAMP" ] && last=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$last" in ''|*[!0-9]*) last=0 ;; esac
if [ $((now - last)) -lt "$COOLDOWN" ]; then
    log SKIP "unhealthy($reason) but cooldown $((now - last))s < ${COOLDOWN}s"
    exit 0
fi

# ---- 清零 resourceCount / startCount ----
reset_latch() {
    python3 - "$STATUS_FILE" <<'PY'
import io
import sys
import time

path = sys.argv[1]
now = str(int(time.time()))
lines = []
for line in io.open(path, encoding="utf-8").read().splitlines():
    fields = line.split("|")
    if len(fields) == 3 and fields[1] in ("resourceCount", "startCount"):
        fields[0] = now
        fields[2] = "0"
    lines.append("|".join(fields))
io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
PY
}

before=""
if [ -f "$STATUS_FILE" ]; then
    before=$(tr '\n' ' ' < "$STATUS_FILE")
    cp -a "$STATUS_FILE" "$STATUS_BAK"
fi

log ACT "reason=$reason before=[$before] -> stop, reset latch, start"
systemctl stop cloudmonitor >/dev/null 2>&1
sleep 2
[ -f "$STATUS_FILE" ] && reset_latch
systemctl start cloudmonitor >/dev/null 2>&1
echo "$now" > "$STAMP"

# 给父进程 20 秒拉子进程（实测 15~25 秒）
for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 2
    child=$(pgrep -fc "^$BASE/bin/argusagent$" 2>/dev/null || true)
    [ "${child:-0}" -ge 1 ] && break
done
child=${child:-0}
after=$( [ -f "$STATUS_FILE" ] && tr '\n' ' ' < "$STATUS_FILE" || true )

if [ "$child" -ge 1 ]; then
    log RECOVER "reason=$reason child=$child after=[$after]"
    exit 0
fi

log FAIL "reason=$reason still_no_child after=[$after]"
tail -n 8 "$AGENT_LOG" 2>/dev/null | sed 's/^/    log: /' >> "$LOG"
exit 1
