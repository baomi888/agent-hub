#!/usr/bin/env bash
# hybrid 后端上线后的契约验收：一条命令跑完，输出「能不能对接」。
#
# 为什么需要：前端是**照接口文档 v1** 写的，而后端代码在林宏亮本地、他推上来之前
# 我无法验证任何一条真响应。与其两个人来回试，不如把「我这边依赖了什么」变成可执行检查。
#
# 用法（本地或服务器上都行，前提是本地前后端已起）：
#   bash tools/check_hybrid_contract.sh
#   BASE=http://127.0.0.1:8000 bash tools/check_hybrid_contract.sh
#
# ⚠️ 需要登录 Cookie。取法：浏览器登录后 F12 → Network → 任一 /api/* 请求 →
#    复制 Request Headers 里的 Cookie，整段粘到下面 HYB_COOKIE 环境变量。
set -u
BASE="${BASE:-http://127.0.0.1:8000}"
COOKIE="${HYB_COOKIE:-}"

pass=0
fail=0
failed_names=""

ok()   { pass=$((pass+1)); printf 'PASS  %s\n' "$1"; }
bad()  { fail=$((fail+1)); failed_names="$failed_names | $1"; printf 'FAIL  %s  %s\n' "$1" "${2:-}"; }

req() { # method path [body] -> 输出 "HTTP_CODE<TAB>body"
  local m="$1" p="$2" b="${3:-}"
  if [ -n "$b" ]; then
    curl -s -m 15 -X "$m" "$BASE$p" -H "Content-Type: application/json" \
      ${COOKIE:+-H "Cookie: $COOKIE"} -d "$b" -w '\n%{http_code}' 2>/dev/null
  else
    curl -s -m 15 -X "$m" "$BASE$p" ${COOKIE:+-H "Cookie: $COOKIE"} \
      -w '\n%{http_code}' 2>/dev/null
  fi
}
code_of() { printf '%s' "$1" | tail -n1; }
body_of() { printf '%s' "$1" | sed '$d'; }

check_list() { # 名称 路径 期望key
  local name="$1" path="$2" key="$3"
  local r code body
  r=$(req GET "$path")
  code=$(code_of "$r"); body=$(body_of "$r")
  if [ "$code" = "404" ]; then
    bad "$name" "端点 404 —— 后端还没上线或路径不对"
  elif [ "$code" != "200" ]; then
    bad "$name" "HTTP $code（未登录可能返回 401/403）"
  elif printf '%s' "$body" | grep -q "\"$key\""; then
    if printf '%s' "$body" | grep -q '"items"'; then
      ok "$name（{items:[...]} 壳层 ✓）"
    else
      ok "$name"
    fi
  else
    bad "$name" "200 但响应里没有 $key 字段：$(printf '%s' "$body" | head -c 120)"
  fi
}

echo "=== hybrid 契约验收（BASE=$BASE）==="
if [ -z "$COOKIE" ]; then
  echo "⚠️  没设 HYB_COOKIE。未登录时多数端点会 401 ⇒ 下面会一片 FAIL，"
  echo "    那不代表代码有问题。登录后按文件头说明设置该变量再跑。"
  echo
fi

# ---------- 0. 前置判断：端点是否已上线 ----------
# 全是 404 时**不要**把一堆 FAIL 甩给林宏亮 —— 那会让他以为前端在乱报。
# 先给一句结论，然后安静退出。
_r=$(req GET "/api/hybrid/workflows")
_c=$(code_of "$_r")
if [ "$_c" = "404" ]; then
  echo "=== 结论：后端 hybrid 端点还没上线（/api/hybrid/workflows 返回 404）==="
  echo
  echo "这不是前端问题。hybrid/ 与 api/hybrid.py 目前不在任何远端分支上，"
  echo "所以下面这些检查全部无法进行。等代码推上来后重跑本脚本即可。"
  echo
  echo "在那之前，前端会显示「接口待上线」+ 演示数据（三态降级），不会白屏。"
  echo
  echo "已知的 3 个待对齐点（来自 docs/接口契约对齐_委托与总结_1014.md，"
  echo "与 hybrid 无关但同属 T5 契约统稿）："
  echo "  1. delegate 字段名：_out() 给 delegation_id/prompt/deliver_sid，"
  echo "     前端用 id/instruction/session_id（id 对不上 ⇒ 按钮全点不动）"
  echo "  2. runs 列表壳层是 {items:[...]}，不是 {runs:[...]}"
  echo "  3. delegate 路由带尾斜杠、前端不带，建议去掉（对齐其余 35 个端点）"
  exit 0
fi

echo
echo "=== 下面是契约明细 ==="

# ---------- 1. 管理端点：路径 + 返回壳层 ----------
echo "--- 管理端点（12 端点的 GET 部分）---"
check_list "GET /api/hybrid/workflows（列表）"        "/api/hybrid/workflows"  "items"
check_list "GET /api/hybrid/rules（规则列表）"        "/api/hybrid/rules"      "items"
check_list "GET /api/hybrid/runs（执行记录）"         "/api/hybrid/runs"       "items"

# ---------- 2. 归属与 404 语义 ----------
echo "--- 错误语义 ---"
r=$(req GET "/api/hybrid/workflows/deadbeefdead"); code=$(code_of "$r")
if [ "$code" = "404" ]; then ok "不存在的 workflow → 404（符合预期）"
else bad "不存在的 workflow" "期望 404，实际 $code"; fi

r=$(req DELETE "/api/hybrid/workflows/builtin_plan_execute"); code=$(code_of "$r")
if [ "$code" = "404" ]; then ok "DELETE 内置工作流 → 404（内置只读 ✓）"
else bad "DELETE 内置工作流" "文档说应 404，实际 $code"; fi

# ---------- 3. 非法定义 → 400（前端表单要能显示 detail）----------
echo "--- 校验 ---"
r=$(req POST "/api/hybrid/workflows" \
  '{"name":"契约测试","description":"","definition":{"nodes":[{"id":"a","type":"llm"},{"id":"b","type":"tool"}],"edges":[{"source":"a","target":"zzz"}]}}')
code=$(code_of "$r"); body=$(body_of "$r")
if [ "$code" = "400" ]; then
  if printf '%s' "$body" | grep -q '"detail"'; then
    ok "非法定义 → 400 且带 detail（前端能显示原因）"
  else
    bad "非法定义 400 的 detail" "没有 detail 字段：$(printf '%s' "$body" | head -c 100)"
  fi
else
  bad "非法定义" "期望 400，实际 $code（前端会拿到看不懂的错）"
fi

r=$(req PUT "/api/hybrid/rules/__contract_probe__" \
  '{"signal":"requires_agent","action":"to_agent","condition":"shared.x >"}')
code=$(code_of "$r")
if [ "$code" = "400" ]; then ok "规则 condition 语法错误 → 400"
else bad "规则 condition 语法错误" "期望 400，实际 $code"; fi

# ---------- 4. 字段名一致性（★ 最关键，字段对不上前端列表有数据但按钮全点不动）----------
echo "--- ★ 字段名一致性（前端 types.ts 依赖）---"
r=$(req GET "/api/hybrid/workflows"); body=$(body_of "$r")
first_id=$(printf '%s' "$body" | grep -o '"id"[[:space:]]*:[[:space:]]*"[a-f0-9]\{12\}"' | head -1 | grep -o '[a-f0-9]\{12\}')
if [ -n "$first_id" ]; then
  ok "列表返回的 id 是 12 位 hex（文档 §5.1 要求后端生成）"
  r=$(req GET "/api/hybrid/workflows/$first_id")
  code=$(code_of "$r"); body=$(body_of "$r")
  for f in name description definition version created_at updated_at builtin; do
    if printf '%s' "$body" | grep -q "\"$f\""; then
      ok "WorkflowRow 含 $f"
    else
      bad "WorkflowRow 缺 $f" "前端类型里声明了它"
    fi
  done
else
  # ⚠️ 同理：列表端点 404 时不能报「字段缺失」，那是误报。
  code=$(code_of "$r")
  if [ "$code" != "200" ]; then
    bad "WorkflowRow 字段核对" "列表端点返回 $code，字段无法核对（先等后端上线）"
  else
    echo "SKIP  取不到工作流 id（可能一个都没有）—— 字段核对需要至少 1 条数据"
  fi
fi

# ---------- 5. 规则列表只返回 enabled=1（文档 §5.2 的坑）----------
echo "--- 规则列表语义 ---"
r=$(req GET "/api/hybrid/rules"); code=$(code_of "$r"); body=$(body_of "$r")
if [ "$code" != "200" ]; then
  # ⚠️ 端点还没上线时 body 是空的，报「缺 builtin」是**误报** ——
  # 会让林宏亮去改本来正确的字段。第一版就踩了这个坑（404 → 报字段缺失）。
  bad "规则字段核对" "端点返回 $code，字段无法核对（先等后端上线）"
else
  if printf '%s' "$body" | grep -q '"builtin"'; then
    ok "RuleRow 含 builtin（前端要区分系统默认 / 用户覆盖）"
  else
    bad "RuleRow 缺 builtin" "前端无法区分哪些是内置只读"
  fi
  if printf '%s' "$body" | grep -q '"cooldown_seconds"'; then
    ok "RuleRow 含 cooldown_seconds"
  else
    bad "RuleRow 缺 cooldown_seconds" "文档 §5.2 明确有这个字段"
  fi
fi

# ---------- 汇总 ----------
echo
echo "===================================="
echo "通过 $pass 项，失败 $fail 项"
if [ "$fail" -gt 0 ]; then
  echo "失败项：$failed_names"
  echo
  echo "怎么给林宏亮：把上面 FAIL 的行原样贴给他即可（每行都写了期望值）。"
  echo "其中「字段名一致性」那组最要紧 —— 字段对不上时页面能显示数据，"
  echo "但所有按 id 的按钮（编辑/暂停/删除/预览）都会点不动，且不报错。"
fi
[ "$fail" -eq 0 ] && echo "全部通过 ⇒ 前端可以直接对接，不用改代码。"
exit 0
