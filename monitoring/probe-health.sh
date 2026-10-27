#!/usr/bin/env bash
# ============================================================
#  组件健康探针：写 Prometheus textfile 指标
#
#  由 cron 每分钟执行：
#      * * * * * /root/monitoring/probe-health.sh >/dev/null 2>&1
#
#  产出 /var/lib/node_exporter/textfile/agent_hub.prom：
#      agent_hub_up{component="backend"}  1|0
#      agent_hub_up{component="frontend"} 1|0
#      agent_hub_up{component="worker"}   1|0
#
#  这样「页面正常但 worker 静默死亡」也能被 AgentHubComponentDown 规则抓到。
# ============================================================
set -uo pipefail

OUT_DIR="${OUT_DIR:-/var/lib/node_exporter/textfile}"
OUT="$OUT_DIR/agent_hub.prom"
mkdir -p "$OUT_DIR" 2>/dev/null || true

bc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo 000)
fc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ 2>/dev/null || echo 000)

if pgrep -f "worker.scheduler" >/dev/null 2>&1; then w=1; else w=0; fi
[ "$bc" = "200" ] && b=1 || b=0
[ "$fc" = "200" ] && f=1 || f=0

tmp="$OUT.$$"
{
  echo "# HELP agent_hub_up 组件健康 1=ok 0=down"
  echo "# TYPE agent_hub_up gauge"
  echo "agent_hub_up{component=\"backend\"} $b"
  echo "agent_hub_up{component=\"frontend\"} $f"
  echo "agent_hub_up{component=\"worker\"} $w"
} > "$tmp" && mv -f "$tmp" "$OUT"
