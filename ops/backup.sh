#!/usr/bin/env bash
# 每日备份：SQLite 三个库 + 知识库注册表 + 向量库，保留最近 7 份。
#
# 用法（一条命令）：
#   bash ops/backup.sh
#
# 挂到 cron（每天 04:00）：
#   (crontab -l 2>/dev/null; echo "0 4 * * * bash /root/ops/backup.sh >> /root/backups/backup.log 2>&1") | crontab -
#
# ★ 为什么不用 cp 直接拷 .db：SQLite 开着 WAL 时，cp 出来的可能是"半个事务"，
#   恢复时轻则少几条记录，重则整库损坏。这里用 sqlite3 的 backup API，
#   它保证拿到一个一致的快照，且不会阻塞正在跑的读写。
set -uo pipefail

ROOT="${BAOMI_ROOT:-/root}"
DEST="${BAOMI_BACKUP_DIR:-$ROOT/backups}"
KEEP=7
DAY="$(date +%F)"
OUT="$DEST/$DAY"

mkdir -p "$OUT" || { echo "[中止] 建不了备份目录 $OUT"; exit 1; }

PY=""
if [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "[中止] 找不到 python3，无一致性备份能力（不要用 cp 兜底）"
  exit 1
fi

echo "===== 备份到 $OUT ====="

# ---- 1. SQLite：用 backup API 取一致快照 ----
backup_db() {
  local name="$1"
  local src="$ROOT/data/$name"
  if [ ! -f "$src" ]; then
    echo "  跳过 $name（文件不存在）"
    return 0
  fi
  "$PY" - "$src" "$OUT/$name" <<'PYEOF'
import sqlite3
import sys

src, out = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(out)
s.backup(d)
d.close()
s.close()
PYEOF
  if [ $? -eq 0 ]; then
    echo "  ok  $name  ($(du -h "$OUT/$name" | cut -f1))"
  else
    echo "  [!] $name 备份失败"
  fi
}

backup_db sessions.db     # 用户 / 会话 / 消息 / 四六级 / 学习路径
backup_db scheduler.db    # 定时任务与 job_runs 记账
backup_db hybrid.db       # 混合架构工作流与切换规则

# ---- 2. 知识库注册表（库名 / 文件清单 / 归属）----
for f in "$ROOT"/data/kb_*.json; do
  [ -f "$f" ] || continue
  cp -f "$f" "$OUT/" && echo "  ok  $(basename "$f")"
done

# ---- 3. 向量库（知识库正文切片，丢了就要用户重新上传，最不可再生）----
if [ -d "$ROOT/chroma_db" ]; then
  tar -czf "$OUT/chroma_db.tar.gz" -C "$ROOT" chroma_db 2>/dev/null &&
    echo "  ok  chroma_db.tar.gz  ($(du -h "$OUT/chroma_db.tar.gz" | cut -f1))" ||
    echo "  [!] chroma_db 打包失败"
else
  echo "  跳过 chroma_db（目录不存在）"
fi

# ---- 4. 轮转：只留最近 $KEEP 份 ----
COUNT=$(find "$DEST" -maxdepth 1 -type d -name '20*-*-*' | wc -l)
if [ "$COUNT" -gt "$KEEP" ]; then
  find "$DEST" -maxdepth 1 -type d -name '20*-*-*' | sort | head -n "$((COUNT - KEEP))" |
    while read -r old; do
      rm -rf "$old" && echo "  清理旧备份 $(basename "$old")"
    done
fi

echo
echo "当前备份："
du -sh "$DEST"/* 2>/dev/null | tail -n "$KEEP"
