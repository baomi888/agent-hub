# Step 1 评审包：SSE 输出事件化（TurnEvent + render）

如果你只想看结论，跳到「三个待决问题」。

---

## 一、这是什么

agent-hub 的一次重构提案。目标：**让业务代码不再直接产出「SSE 编码后的字符串」**，改为产出「事件对象」，由唯一一处 `render()` 转成线上字节。

## 二、现状：已经做完的部分

`api/sse_protocol.py`（111 行）已经把「帧的字节格式」集中了：

- `_encode(event, data)` 是唯一编码实现；
- 13 个 builder：`status / token / error / title / truncation / sources / grounding / tool / approval / workflow_node / mode_switch / hybrid_start / done`；
- `api/chat.py` 里有 57 处调用，全部形如 `yield sse.status("正在检索…")`。

所以「换传输协议只改一处」这个目标**已经达成**——要改成 WebSocket，只需要改 `_encode()`。

## 三、本次提案要做什么

把「函数调用」改成「事件对象 + 统一渲染」：

```python
# 改前
yield sse.status("正在检索知识库…")

# 改后
yield render(Status("正在检索知识库…"))
```

```python
# api/sse_protocol.py 新增
@dataclass(frozen=True)
class Status:
    text: str

def render(ev) -> str:
    match ev:
        case Status(text=t):  return _encode("status",  {"text": t})
        case Token(text=t):   return _encode("token",   {"text": t})
        case Sources(items=x): return _encode("sources", {"items": x})
        ...
```

## 四、涉及文件：只改这 3 个

| 文件 | 原路径 | 改动 |
|---|---|---|
| `sse_protocol.py` | `api/sse_protocol.py` | +13 个 frozen dataclass、+ `render()`；现有 builder 保留（作为 render 内部实现）或内联 |
| `chat.py` | `api/chat.py` | 57 处 `yield sse.xxx(...)` → `yield render(XxxEvent(...))` |
| `test_sse_protocol.py` | `tests/test_sse_protocol.py` | 现有 11 条字节级断言全部保留（保证 render 输出与今天逐字节一致），新增事件对象断言 |

## 五、必须读但不该动的

放在 `02-read-only/`：

| 文件 | 为什么读 |
|---|---|
| `sse.ts` | `frontend/src/lib/sse.ts`，SSE 帧解析器，**协议的真正消费方** |
| `page.tsx` | `frontend/src/app/page.tsx`，每个帧名对应一个 `case`，能看出各帧的用途和顺序要求 |
| `types.ts` | `frontend/src/lib/types.ts`，帧 payload 的 TS 类型定义（契约） |
| `test_baseline_chat_stream.py` | `tests/test_baseline_chat_stream.py`，RAG / LLM 两条路径的帧序列基线 |

## 六、护栏：不该动，而且它们就是验收标准

放在 `03-guardrails/`：

| 文件 | 作用 |
|---|---|
| `golden_stream.py` | 快照录制/比对工具 |
| `test_stream_golden.py` | rag / llm 两条路径的快照用例 |
| `golden-rag.json`、`golden-llm.json` | 当前线上字节的完整快照 |

事件化**不改变线上字节**，所以这些快照必须一字不差地继续通过。它们比人工 review 更可靠。

---

## 七、三个待决问题（想听意见的地方）

### 1. 值不值得做

现在 `sse.status("x")` 已经让业务层不知道帧格式了（不知道 event 名、不知道 payload 字段）。事件化额外买到的只有三样：

1. **统一出口后处理**——脱敏 / 采样 / 审计日志可以从「57 处插」变成「1 处」；
2. **事件级测试**——单测断言 `[Status, Sources, Token, Done]`，不用解析 SSE 字符串；
3. **多消费者**——一次执行同时喂 SSE 和审计/回放。

代价：**代码量增加**（`render(Status("x"))` 比 `sse.status("x")` 长），多一层间接。

如果近期没有多协议、多消费者的需求，是不是可以不做？这一条最需要被挑战。

### 2. 事件对象放哪

- **A. 放 `api/sse_protocol.py` 里**：使用方只 import 一个模块，最省事；但协议层职责变成「事件定义 + 编码」两种。
- **B. 单独 `api/events.py`**：事件是业务概念、协议只是它的一个消费者，方向更正确；但每个调用点要 import 两个模块（或用 `sse.render` 再导出）。

倾向 B，但如果 B 会让「换协议只改一处」变成「换协议要改 import」，那就得不偿失。

### 3. 57 处改动怎么保证行为不变

计划用两层护栏：

- `tests/golden/*.json` 逐字节一致（帧名、字段、顺序、中文不转义、帧尾空行）；
- `test_baseline_chat_stream.py` 的帧名列表不变。

这两层够不够？还需要别的吗（比如对 `render()` 做穷举式单测）？

---

## 八、两条硬约束（评审时请特别看）

1. **`yield` 不能委托给普通函数**。异步生成器里必须 `yield render(ev)`，不能写一个普通函数替它 yield。所以调用点仍然是两段式，无法进一步简化——如果期望「改完代码变短」，那预期要调整。

2. **帧顺序是硬约定**，不是巧合：
   - `grounding` 必须早于第一个 `token`（前端要在正文上方挂「依据较弱」提示条）；
   - `done` 必须最后（前端收到它才 `player.finish()` 收尾）；
   - `truncation` 在 `token` 之后、`done` 之前。

   依据在 `page.tsx` 和 `api/chat.py` 的注释里，评审时可以对照。
