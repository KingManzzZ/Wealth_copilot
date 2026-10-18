# 生成中止与取消 · 问题与解决方案

> **模块定位**：用户点「停止生成」或直接关掉页面时，一轮生成如何**真正停下来**：
> 不再向 LLM 要 token、不再执行新工具、会话锁及时释放、半截回答怎么落库与回放。
>
> **范围**：`backend/api/routes/chat.py`、`backend/core/agent/agent.py`、
> `backend/core/agent/cancel.py`、`backend/core/services/{conversations,replay}.py`、
> `backend/core/database/schema.py`、`frontend/src/{hooks/useChat.ts,lib/api.ts}`。
>
> **测试**：[`../backend/tests/cancel/`](../backend/tests/cancel/)（4 层 / 23 用例，标准库 runner）。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 |
| :--- | :--- | :--- | :--- |
| S1-1 | 断连后清理不执行：落库、解锁、关流都挂在"可能被延迟终结"的生成器 `finally` 上 | 🔴 高 | ✅ 已修复（有实测） |
| S1-2 | Agent 循环没有取消检查点：断连后仍可能再发 LLM 请求、再跑工具 | 🟠 中 | ✅ 已修复（有实测） |
| S1-3 | 被中止的半截轮次会污染下一轮上下文（模型以为自己说完了） | 🟠 中 | ✅ 已修复（策略 B） |
| S1-4 | 前端"已停止"与库里的记录是两份真相；取消后立刻重发会撞 409 | 🟡 低 | ✅ 已修复 |

---

### S1-1 · 断连后清理不执行

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-18 |
| **发现方式** | 读 Starlette 源码 + 等价复现两条断连路径 |
| **严重度** | 🔴 高（成本 + 账号锁死） |
| **状态** | ✅ 已修复（route 测试覆盖） |

#### 🔍 现象

用户点停止 / 关标签页后，出现三种"看不见的残留"：

1. 同一会话再发消息一直 **409**（"正在生成中"）—— 会话锁没释放；
2. 半截回答**不落库** —— 前端显示过、库里没有；
3. `openai` 的流没关 —— 供应商那边还在生成，token 继续计费。

#### 🧪 证据

`api/routes/chat.py` 原实现把收尾放在**同步生成器**的 `finally` 里：

```python
def event_stream():
    try:
        for event in chat_stream(...):
            yield sse_(event)
    finally:
        _persist(...)     # 落库
        _release(...)     # 解锁
```

而 `StreamingResponse` 对同步生成器走 `iterate_in_threadpool`
（`starlette/concurrency.py:51-59`）：

```python
yield await anyio.to_thread.run_sync(_next, as_iterator)   # 没有 finally: close()
```

两个事实叠加，导致 `finally` 不可靠：

- `run_sync` 默认 `abandon_on_cancel=False` → 正在执行的 `next()` **无法被中断**
  （取消要等工具/等待返回）；
- Starlette 有两条断连路径（`responses.py:267-280`），行为不同：

| 路径 | 断连后 | 生成器 `finally` |
| :--- | :--- | :--- |
| ASGI spec ≥ 2.4（`send` 抛 OSError） | 立即停止 | 立即执行（实测与断连同一时刻） |
| ASGI spec < 2.4（task group 取消） | **多吐 1 个 chunk** | **等循环 GC**：0.6s 内未执行，`gc.collect()` 后 0.85s 才执行 |

等价复现（`starlette 1.6.0`）：

```text
('disconnect', 0.29) ('yield', 3, 0.33)   ← 取消后还多产出一个事件
('after_sleep', finally? False)           ← 0.5s 后仍未收尾
('finally', 0.85)                         ← 只有显式 gc.collect() 才触发
```

即：清理是否发生，**取决于 Python 的循环 GC 时机**——这不是契约，是巧合。

#### 🎯 根因

**把资源清理挂在了"生命周期不由自己控制的对象"上。**
生成器何时被 `close()` 由框架的实现细节与 GC 决定；而落库、解锁、关流是**必须发生**的。

#### ✅ 解决方案

改成 **worker 线程生产 / async 消费**（`api/routes/chat.py`）：

```
worker 线程：chat_stream(...) ──push──► asyncio.Queue ──► async SSE 生成器
                 │                                        │
                 └ finally: 落库 + 解锁 + mark_done        └ finally: cancel + 停 watcher
```

- 线程的 `finally` 是**确定性**的：线程结束就是结束，不受 GC 影响；
- 跨线程投递用 `loop.call_soon_threadsafe(queue.put_nowait, item)`，worker 不阻塞；
- 嵌套 `try/finally` 保证即使落库自身抛错，**解锁与 `mark_done` 仍会执行**；
- DB 调用改走 `asyncio.to_thread`：路由变成 `async def` 之后，SQLite 的阻塞 I/O
  不能再靠 FastAPI 自动丢线程池（那是同步 `def` 才有的待遇）。

#### ✔️ 验证

`tests/cancel/test_chat_route_cancel.py`：

- 取消接口返回 `done=true` 且实测 **31ms** 收尾（假 LLM 每 chunk 10ms）；
- 取消后同会话立刻开新一轮 → **200**（不再 409）；
- 落库行 `interrupted=1`、`stop_reason=client`、内容为已生成部分。

#### 💡 学习要点

1. **框架的"取消"不等于你的清理被执行。** 同步生成器尤其危险——它没有 `finally: close()` 的保证。
2. 需要"必须发生"的收尾，就把它放在**你能控制生命周期**的地方（线程/显式上下文管理器）。
3. 判断"是否真的停了"要看**可等待信号**（`done`），不要看"流断了没有"。

---

### S1-2 · Agent 循环没有取消检查点

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-18 |
| **严重度** | 🟠 中（成本 / 无谓 GPU 占用） |
| **状态** | ✅ 已修复（单元测试覆盖） |

#### 🔍 现象

即使链路层能收到"取消"，`agent.chat_stream` 里也没有任何地方读它：

- 第 1 轮被取消后，循环仍可能进入第 2 轮，再发一次 LLM 请求；
- 一次工具批次里剩下的工具仍会全部执行（RAG 单次 1~3s、持 GPU 锁）。

#### 🎯 根因

`chat_stream` 是一个纯拉取式生成器：**只有"下一个人来 next()"才前进**。
框架层取消后没人再 next，于是它停在 `yield` 上，既不会主动退出，也不会自检。

#### ✅ 解决方案

`core/agent/cancel.py` 提供 `TurnCancel`（`threading.Event` + reason）与
`TurnCancelled`（控制流异常，不是错误）。`agent.py` 在**四个位置**检查：

| 位置 | 目的 |
| :--- | :--- |
| 每轮开头 | 省掉最贵的一次 LLM 请求 |
| 每个流式 chunk | 立刻跳出并 `stream.close()`，停掉供应商侧计费 |
| 每个工具执行前 | 不启动新的 1~3s GPU 检索 |
| 工具执行后 | **不检查**——已产生的副作用必须连同结果一起回填/落库 |

#### ✔️ 验证

`tests/cancel/test_agent_cancel.py`：

- 开跑前取消 → `call_count == 0`；
- 流中取消 → 上游 `stream.closed is True`、chunk 停在第 2 个、`call_count == 1`；
- 工具循环中取消 → 只执行第一个工具、不再发起下一轮、已执行的 `tool_result` 仍产出。

#### ⚠️ 边界（做不到的部分）

Python 线程无法安全强杀。取消发生在工具执行**中**时，要等这次调用返回
（RAG 重排最长 1~3s）。这是检查点方案的固有限制，不假装能中断。

---

### S1-3 · 半截轮次污染上下文

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-18 |
| **严重度** | 🟠 中（"记忆杂乱"的直接来源） |
| **状态** | ✅ 已修复（回放测试覆盖） |

#### 🔍 现象

停止后，库里存着一条**半截回答**。下次对话时它被当作正常历史回放给模型：

- 模型以为自己"已经把话说完了"，可能接着半句话往下讲；
- 这一轮的工具可能只执行了一部分，回放时会与 `tool_calls` 语义不符。

#### ✅ 解决方案（产品决策：策略 B）

**被中止的 assistant 轮次整轮不进模型上下文**，但**用户消息保留**：

- 落库时带 `interrupted=1` + `stop_reason`（`messages` 表新增两列，旧库 `ALTER TABLE` 迁移）；
- `services/replay.py` 在展开协议形态时 `continue` 掉该行 —— 它嵌着的 `tool_calls`
  一起消失，不会留下"有 tool_calls 无 tool 应答"的非法历史；
- 保留用户消息：否则模型会以为用户那句话没说过，下一轮容易重复追问
  （协议上"连续两条 user"合法）。

> 备选策略 A（保留并加"（本轮被中断）"前缀）更适合"用户只是嫌慢、马上要接着说"的场景。
> 本项目选 B，因为半截文本对模型的误导大于连续性收益。

#### ✔️ 验证

`tests/cancel/test_memory_replay.py`：回放只剩 `user / user / assistant`，
`prepare_history` 同样排除，且 `info["rows"]` 仍报告原始行数（裁剪是下游的事）。

---

### S1-4 · 两份真相与 409 竞态

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-18 |
| **严重度** | 🟡 低（体验） |
| **状态** | ✅ 已修复 |

#### 🔍 现象

- 前端把消息标成"已停止"，但服务端落库的是"取消生效那一刻"的内容 —— 两边不一致；
- 用户点停止后立刻发下一条 → 409（上一轮 worker 还在收尾）。

#### ✅ 解决方案

- `POST /api/chat/cancel {turn_id}` 返回 `{cancelled, done, reason}`，
  `done` 表示**已落库 + 已解锁**（最多等 5s）；
- 前端 `stopStreaming` 顺序：**先发 cancel、再 abort 本地流**。反过来服务端会先看到断连，
  `stop_reason` 记成 `disconnect`，事后分不清"用户停的"还是"掉线"；
- 等待期间 `isStopping` 禁用发送（按钮显示 spinner），避免撞 409；
- 历史消息带 `interrupted`，刷新后仍显示"已停止生成"；
- 取消接口**幂等**：未知/已结束的 `turn_id` 返回 `{cancelled:false, done:true, reason:"not_running"}`。

#### 💡 学习要点

**"停止"是一个跨越三个系统的状态机**（浏览器 AbortController / 服务端生成循环 / 数据库），
必须有一个**权威的完成信号**。前端自己"觉得停了"不算数。

---

## 测试框架

新增 [`backend/tests/cancel/`](../backend/tests/cancel/)，按依赖成本分层：

| 文件 | 覆盖 | 依赖 |
| :--- | :--- | :--- |
| `test_cancel_token.py` | 幂等、先到先得、检查点异常、完成信号 | 无 |
| `test_agent_cancel.py` | 四个检查点、上游关流、工具边界 | openai（假客户端） |
| `test_memory_replay.py` | interrupted 落库/回放、旧库迁移 | 临时 SQLite |
| `test_chat_route_cancel.py` | 409、取消接口、断连 watcher、锁释放 | fastapi + httpx（缺失自动跳过） |

设计要点：

1. **不依赖 pytest**：断言与执行器在 `tests/_harness.py`（标准库），
   `python tests/cancel/run_tests.py` 在任何 Python 3.10+ 都能跑；
2. **每个用例一个一次性数据库**（`_support.temp_database()`），绝不写
   `backend/db/data.db`；
3. **假 LLM 必须可控延迟**：没有延迟时"取消"会被测成"流刚好跑完了"，断言失去意义；
4. 路由层缺 `fastapi/httpx` 时**整文件跳过并说明原因**，不让环境问题伪装成 12 个错误。

```powershell
cd backend
python tests/cancel/run_tests.py            # 全部（无 fastapi 时路由层跳过）
python tests/cancel/run_tests.py -k token   # 只跑取消令牌
```

实测：**23 通过 / 0 失败 / 0 跳过**（含路由层，装齐 fastapi + httpx 时）。

## 未做 / 待办

| 项 | 说明 |
| :--- | :--- |
| 取消接口鉴权 | 全项目无鉴权；多用户时须校验 `turn_id` 归属，否则可取消他人轮次 |
| 工具中途取消 | 只能检查点，不能强杀（见 S1-2 边界） |
| legacy 兼容路径取消 | 只传 `history` 的旧路径不注册轮次，取消对它无效（该路径也不落库、无锁） |
| 取消等待上限 | 5s 是拍的：worker 卡在工具里时够用，但更长的工具要重新评估 |
| **回滚已发生的写入** | 取消只停生成、不撤销副作用。**撤回**（对话 + 数据）已实现：`POST /api/conversations/{id}/withdraw`，详见 [`../Investigation/单轮次状态回滚/`](../Investigation/单轮次状态回滚/) |
