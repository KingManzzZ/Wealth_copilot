# WealthPilot 前后端 API 契约

前端已用 mock 数据独立跑通，后端 FastAPI 按此契约实现即可对接。
所有接口前缀 `/api`，Vite dev 已配 proxy 到 `http://localhost:8000`。

## 1. `GET /api/health`

健康检查。前端用它判断后端是否在线，决定是否降级到 mock。

- 响应：`200 OK`，body 任意（前端只看状态码）

## 2. `GET /api/profile`

获取用户画像（跨会话长期记忆）。

- 响应 200：
```json
{
  "user_id": "user-1",
  "name": "学习者",
  "age": 29,
  "city": "上海",
  "occupation": "后端工程师",
  "industry": "互联网",
  "education": "硕士",
  "family": "单身",
  "monthly_income": 18000,
  "monthly_expense": 9500,
  "assets": { "现金及活期": 80000, "基金": 30000, "股票": 20000 },
  "debts": { "房贷": { "remaining": 800000, "monthly_payment": 6500 } },
  "goals": [
    { "id": "g1", "name": "二套房首付", "target": 500000, "current": 130000, "deadline": "2031-09-16", "priority": "high" }
  ],
  "risk_tolerance": "balanced",
  "investing_years": 5,
  "liquidity_needs": "medium",
  "marital_status": "未婚",
  "last_updated": "2026-09-22T10:30:00"
}
```

`children`、`supporting_parents` 未提供时前端显示「待确认」（允许省略）。完整字段定义、枚举量化标准与完善度规则见 `backend/API.md`；前端类型见 `src/types/index.ts` 的 `UserProfile`。`priority` ∈ `high|medium|low`。

## 3. `GET /api/dashboard`

仪表盘聚合数据，一次返回所有模块所需。

- 响应 200：
```json
{
  "kpis": { "monthly_income": 18000, "monthly_expense": 9500, "monthly_surplus": 8500, "saving_rate": 0.472 },
  "cashflow": [
    { "month": "4月", "income": 18000, "expense": 9200, "surplus": 8800 }
  ],
  "goals": [ /* 同 profile.goals */ ],
  "asset_liability": [
    { "name": "现金及活期", "value": 80000, "kind": "asset" },
    { "name": "房贷", "value": 800000, "kind": "liability" }
  ],
  "health": {
    "overall": 72,
    "grade": "B",
    "dimensions": [
      { "name": "储蓄率", "score": 78, "advice": "储蓄率 47%，高于 30% 健康线。" }
    ]
  }
}
```

`saving_rate` 是 0-1 的小数（前端会 ×100 显示）。`grade` ∈ `A|B|C|D`。`kind` ∈ `asset|liability`。

## 4. `POST /api/chat`（SSE 流式）

核心：对接现有 `../backend/core/agent/agent.py` 的 tool_calls 循环。

- 请求 body：
```json
{
  "message": "我的月度现金流怎么样？",
  "conversation_id": 12,
  "turn_id": "1f0c9e2a-…",
  "history": [{ "role": "user", "content": "..." }]
}
```
- `conversation_id`：新前端必传（服务端读库、裁剪、落库）；不传则走兼容模式或新建会话。
- `turn_id`：本轮 id，前端生成（UUID）。停止按钮拿它调取消接口；不传时服务端补一个，
  并在首帧 `turn` 事件里回显。
- `history`：仅兼容旧前端；带 `conversation_id` 时被服务端忽略（安全边界）。

- 响应：`Content-Type: text/event-stream`，每行 `data: <JSON>\n\n`，结尾 `data: [DONE]\n\n`。

### SSE 事件类型（前端 `ChatStreamEvent` 已对齐）

| type | 字段 | 说明 |
|------|------|------|
| `turn` | `turn_id: string` | **首帧**：本轮 id，取消接口按它定位 |
| `thinking` | `content: string` | LLM 思考片段。前端接收但不展示（可留作未来可视化） |
| `tool_call` | `id, name, arguments` | Agent 决定调用工具。`arguments` 是已解析的对象 |
| `tool_result` | `id, result, status` | 工具执行返回。`status` ∈ `success\|error` |
| `answer` | `content: string` | 最终回答的增量片段（可多次下发，前端拼接） |
| `round` | `round: number` | Agent 进入第 N 轮循环 |
| `cancelled` | `turn_id, reason` | 服务端确认中止。`reason` ∈ `client\|disconnect` |
| `done` | — | 流正常结束 |
| `error` | `message: string` | 错误 |

> 取消后流可能直接断（客户端已 abort），因此**不能依赖 `cancelled` 事件**判断是否停成功；
> 以 `POST /api/chat/cancel` 的返回为准。

### 停止生成：`POST /api/chat/cancel`

```json
{ "turn_id": "1f0c9e2a-…" }
```

响应（幂等，永远 200）：

```json
{ "cancelled": true, "done": true, "turn_id": "…", "reason": "client" }
```

- `cancelled`：这次调用是否置位了取消信号（重复调用返回 `true`，因为已处于取消态；
  该轮已结束时返回 `false` + `done: true`）。
- `done`：服务端是否**已收尾**（落库 + 释放会话锁）。为 `false` 说明等了 5s 还没收尾，
  前端应先不要发下一条（会撞 409）。
- 该轮被中止后，assistant 消息以 `interrupted: true` + `stop_reason` 落库：
  **UI 照常展示**，但**整轮不进入模型上下文**（回放时跳过）。

### 对应现有 agent.py 的映射

现有 `chat_()` 循环里：
- `llm_response.tool_calls` 非空 → 对每个 `tool_call` 发 `tool_call` 事件，执行后发 `tool_result`
- `llm_response.content` → 拆成 `answer` 增量片段（或一次性发）
- 循环结束 → 发 `done`

建议用 FastAPI 的 `StreamingResponse` + `async def` 生成器实现。DeepSeek SDK 的 `stream=True` 可直接拿到增量 token 转 `answer` 事件。

## 5. 降级约定

前端 `src/lib/api.ts` 在 `/api/health` 失败时自动用 `src/lib/mock-data.ts` 的 mock 数据，并用 `mockStreamChat` 模拟 Agent 循环（含工具调用）。所以后端未实现时前端也能独立演示。

## 6. CORS / Proxy

- 开发：Vite proxy 已配 `/api -> http://localhost:8000`，无需 CORS
- 生产：建议同源部署或后端开启 `CORSMiddleware`
