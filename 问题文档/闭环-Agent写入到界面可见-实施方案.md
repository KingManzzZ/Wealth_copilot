# 打通「Agent 写入 → 界面可见」实施方案

> 来源：`项目整体审查报告.md` §8 第 1 步（CX-2 / CX-3 / CX-7）。
> 目标：Agent 写进数据库的东西，用户在同一页面上立刻看得见。
> 状态：**已评审通过并落地**（见 §8 落地记录）

---

## 0. 验收口径

| # | 操作 | 期望 |
|---|---|---|
| A | 后端在线、库里无资产；对话说「我买了一支基金 3 万」 | 不刷新页面，资产负债卡出现「基金 ¥30,000」 |
| B | 对话说「帮我记一笔今天的午餐 35 元」 | 打开流水抽屉（默认本月）→ 看到「餐饮 · 午餐 · −¥35」 |
| C | 抽屉开着时，在对话里再记一笔 | 抽屉自动出现新行，**无需关闭重开**（所以抽屉不遮挡聊天栏、非模态） |
| D | 抽屉切到空月份 / 回到本月 | 空状态文案（带写入引导）/ 数据回来 |
| E | 后端离线（Mock 模式） | 不报错不白屏；抽屉标注 Mock |
| F | `npx tsc -b`、`npx oxlint` | 0 error |

> 注：A 能成立，因为 `record_asset` 写 `assets` 表，而 `/api/dashboard` 直接读该表。
> B 里 **KPI 数字不会动**——`kpis` 派生自 `monthly_cashflow`，`record_expense` 只写 `transactions`。这是 BK-27，见 §6-C1，也正是「流水抽屉」必须存在的理由。

---

## 1. 现状（已逐行核对，非推测）

| 位置 | 现状 | 对应问题 |
|---|---|---|
| `hooks/useChat.ts:208-230` | `finally` 里已做「清空壳消息 + `refreshConversations()`」，但**没有任何跨组件通知** | CX-2 缺失效机制 |
| `components/dashboard/dashboard.tsx:16-27` | 只在挂载时 `getDashboard()`，此后永不刷新 | CX-2 |
| `components/user-menu.tsx:40-44` | 只在挂载时 `getBasicInfo()`，`update_basic_info` 后仍显示旧值 | CX-2 |
| `lib/api.ts:194-203` | `getTransactions()` 存在，但全项目 **0 处调用** | CX-3 |
| `components/chat/chat-panel.tsx:248-253` | 3 条建议词，全是「查询」，无一条引导写入 | CX-7 |
| `backend/api/routes/transactions.py:20-23` | `GET /api/transactions?month=YYYY-MM` 已就绪，返回按时间倒序的明细；**后端零改动** | — |
| `components/ui/*` | 无 drawer/dialog 组件；但 `@radix-ui/react-dialog@1.1.23` 已在 `package.json` 且 `node_modules` 已安装（从未使用） | — |

---

## 2. 设计决策（请重点审这 5 条）

### D1 · 事件契约

`window.dispatchEvent(new CustomEvent("chat-turn-completed", { detail }))`

```ts
interface ChatTurnDetail {
  conversationId: number | null   // 未落库的轮次为 null（CX-1 兼容路径）
  toolNames: string[]             // 本轮调过的工具名，按调用顺序
}
```

- **为什么带 `toolNames`**：① 给「流水按钮红点」用（可选 B3）；② 排查「这轮到底写了什么」时不必再翻 DOM/网络面板。
- **为什么无条件广播**（不先判断「这轮有没有写」）：写入口不止两个、将来还会加；判错一次就是静默不刷新。消费者只是重拉 1~2 个聚合 GET，代价远小于漏刷。每轮多 2 个请求，可接受。

### D2 · 生产者位置

`useChat.ts` 的 `finally`，在「清空壳消息」之后、`refreshConversations()` 旁边。
`sendMessage` 顶部 `if (!trimmed || isStreaming) return` 两条早退**不会**广播（本来也没发生任何事）。

### D3 · 消费者必须「静默重拉」，**不能**复用 `load()`

`getDashboard()` / `getBasicInfo()` 失败时会**回退 mock**（`api.ts:64`、`api.ts:231`）。
如果直接拿它们做刷新：后端抖一下 → 仪表盘从真实数据**变成** mock 的 72 分/假目标；UserMenu 从真实姓名变成 `mockBasicInfo` 的假值。**这比不刷新更糟**（等于制造 CX-8 的现场）。

→ 新增两个严格版本，失败/离线返回 `null`，不回退：

```ts
export async function refetchDashboard(): Promise<DashboardData | null>
export async function refetchBasicInfo(): Promise<BasicInfo | null>
```

消费端一律 `if (next) setXxx(next)`：失败保持旧值，不清空、不报错。
**首次挂载与「重试」按钮仍走原 `load()`**（mock 回退是那里的既有设计，不动）。

### D4 · 抽屉用 Radix Dialog，不手搓

> **落地时修正**（原方案是「模态抽屉 + 遮罩」，实现时发现会让验收 C 无法成立）：
> 模态会 `aria-hidden` + 禁用指针事件，抽屉一开聊天输入就不可用 ——
> 「一边聊一边看账目滚动」正是这条闭环最想拿到的体验，于是改为 **`modal={false}` 的常驻侧栏**：
> 无遮罩、点对话区不关闭、Esc / × 关闭、宽度 `min(26rem, 92vw)`。
>
> 另一个落地修正：抽屉**开在聊天栏的另一侧**（`chatSide === "right"` → 抽屉在左）。
> 聊天栏 400px、抽屉 416px，同侧会把对话整个盖住。

| 理由 | 说明 |
|---|---|
| a11y 免费 | `aria-labelledby`/Esc 关闭/焦点管理由 Radix 负责 |
| 规避一个真实坑 | `TopBar` 的 `<header>` 有 `backdrop-blur-xl`，`backdrop-filter` 会成为 `fixed` 后代的**包含块**。抽屉若写在 header 里，会被裁成 16px 高。Radix Portal 挂到 body，天然免疫 |
| 动画可保留 | `forceMount` + `AnimatePresence` 是 Radix + framer-motion 的官方组合，与现有 popover 的滑入语感一致 |

**备选**：手搓 `fixed top-0 bottom-0` + `motion.div`（少一个 import），但 Esc/aria/焦点要自己写，且必须挂在 AppShell 而非 TopBar。

### D5 · 抽屉的开关状态放 `AppShell`

`AppShell` 持有 `txOpen`，`TopBar` 收 `onToggleTransactions` 回调，`<TransactionsDrawer side={chatSide === "right" ? "left" : "right"}>` 作为 AppShell 根层的兄弟节点渲染。理由同 D4：**绝不能**在 header 内部渲染。AppShell 本来就持有布局状态（chatSide/chatWidth），不引入新概念。

---

## 3. 改动清单

### A 组 · 必做（严格对应 4 条要求）

| # | 文件 | 改动 | 规模 |
|---|---|---|---|
| A1 | **新增** `src/lib/events.ts` | 事件名常量 + `emitChatTurnCompleted()` + `onChatTurnCompleted()` 订阅器（返回解绑函数） | ~30 行 |
| A1′ | **新增** `src/hooks/useChatTurnCompleted.ts` | 用 `ref` 持有最新 handler、effect deps 为空，避免每次渲染重订阅 | ~12 行 |
| A2 | `src/hooks/useChat.ts` | `finally` 里取 `toolNames`（清壳之前取）并广播；import 事件模块 | +4 行 |
| A3 | `src/lib/api.ts` | 新增 `refetchDashboard()` / `refetchBasicInfo()`（严格版）；顺手修 `getTransactions()` 的 mock 过滤漏洞（见下） | ~27 行 |
| A4 | `src/components/dashboard/dashboard.tsx` | `useChatTurnCompleted(refresh)`；`refresh` = `refetchDashboard().then(d => d && setData(d))`，不碰 `error` | ~8 行 |
| A5 | `src/components/user-menu.tsx` | `useChatTurnCompleted(refreshProfile)`，同上 | ~6 行 |
| A6 | **新增** `src/components/transactions/transactions-drawer.tsx` | 见 §4 明细 | ~220 行 |
| A7 | `src/components/top-bar.tsx` + `src/components/app-shell.tsx` | 顶栏加「流水」pill（`Receipt` 图标）；AppShell 持有 open 状态并渲染抽屉 | ~15 行 |
| A8 | `src/components/chat/chat-panel.tsx:249-253` | 建议词补一条 `"帮我记一笔今天的午餐 35 元"`，**放在第一条** | +1 行 |

**A3 的顺手修复**：`getTransactions()` 离线时直接返回 `mockTransactions` 全量，**不按 month 过滤** → 抽屉切到任何月份都看到 9 月那 3 条。
改为离线时按 `occurred_at.slice(0, 7) === month` 过滤（约 3 行），让「Mock 模式下月份切了会变」这句话成立。

**A8 位置说明**：放第一条，因为「能写入」比「能查询」更能表达能力边界（直接回应 CX-7）。

### B 组 · 可选增强（逐条可砍，建议 B1 + B2 + B3）

| # | 内容 | 为什么建议 |
|---|---|---|
| B1 | `mock-data.ts` 给 `mockTransactions` 补 3 条 **2026-10** 的样例 | 有了 A3 的月份过滤后，Mock 模式抽屉默认本月是空的，「离线也能演示」就演示不出来 |
| B2 | 抽屉行内 hover 出现**删除**（`deleteTransaction` 已有，`api.ts:217`） | 记错了没法改是硬伤；且能顺手验证「写 → 删 → 视图同步」链路 |
| B3 | 「流水」按钮上**新数据红点**（`detail.toolNames` 含 `record_income`/`record_expense` 时点亮，打开抽屉即熄灭） | 用 `toolNames` 的唯一实际用途；回应「Agent 做了什么没有 UI 表达」 |
| B4 | `kpi-overview.tsx:104-121` 的 `CountUp` 在新值上会从 0 起跳 | 刷新时数字先掉到 0 再涨上去。观感像「重算了一遍」，可接受；嫌闪就把起点改成上一个值（~5 行） |

### C 组 · 明确不做

1. **不修 KPI 派生规则**：`kpis` 来自 `monthly_cashflow`（`dashboard.py:59-62`），写流水不改 KPI —— 属 BK-27，是后端第 4 步的活。本次不碰，否则要改 schema 派生逻辑，远超「打通可见性」。
2. 不引入全局状态库 / Context。
3. 不消费 `tool_start`（CX-4）、不渲染 `error` 事件（CX-5）、不停发 `history`（CX-1）——各自独立，不混进本次改动。
4. 不做流水的**新增/编辑表单**：写入路径是对话，不是表单。

---

## 4. 抽屉明细（A6）

**骨架**：`Dialog.Root(modal={false})` → `Dialog.Portal` → `Dialog.Content(asChild)` + `motion.div`（侧滑入）；`ScrollArea` 复用已有 `ui/scroll-area.tsx`。

| 区块 | 内容 |
|---|---|
| 头部 | 标题「交易流水」+ Mock 徽标（离线时）+ 关闭按钮 |
| 月份导航 | `‹ 2026 年 10 月 ›` + 「回到本月」（非当前月才出现） |
| 汇总 | 收入 / 支出 / 结余，由当前列表前端求和；配色沿用 `chart-1`（收入）/ `chart-3`（支出）/ `chart-2`（结余），与 KPI 卡同色系 |
| 列表 | 按日期分组（`10 月 18 日 · 周日`）；行 = 分类 + `source` + 时间/`note` + 金额（收入 `+¥` 青绿、支出 `−¥` 陶土橙）+ hover 删除 |
| 删除 | 两次点击（hover 出 Trash2 → 「删除 / 取消」确认），成功后重拉；失败也重拉回服务端真相 |
| 状态 | 加载骨架 3 行 / 空（「本月还没有流水，试试…」）/ 错误（重试按钮）/ Mock 徽标 |
| 交互 | 非模态：点对话区不关闭；Esc、× 关闭；顶栏「流水」按钮可再次点击收起 |

**数据流**：
- `useEffect([open, month])` → `getTransactions(month)`
- `useChatTurnCompleted` → 仅 `open` 时重拉当前月（`keep=true`，不闪骨架）
- 默认 `month` = 本地当前月（今天为 `2026-10`）
- 待确认删除的状态带月份键，切月自动失效（不在 effect 里 setState）

---

## 5. 实施顺序（约 3~4 小时）

1. `events.ts` + `useChatTurnCompleted.ts`（无 UI 风险，可独立合）
2. `useChat` 广播 + `api.ts` 严格重拉
3. Dashboard / UserMenu 订阅 → **先验验收 A**（最容易看到价值的一条）
4. 抽屉 + 顶栏入口 + AppShell 接线
5. 空状态建议词
6. `tsc -b` + `oxlint` + 手工跑 §0 的 A~F

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 事件名字符串两处写死不同步 | 只从 `events.ts` 导出常量，任何文件不写字面量 |
| 刷新把 mock 数据「顶替」真实数据 | D3：严格版 `refetchXxx`，失败保持旧值 |
| 抽屉被 `header` 的 `backdrop-filter` 裁切 | D4/D5：AppShell 根层 + Radix Portal |
| 每轮多 2 个请求 | 聚合 GET，毫秒级；若测出抖动再加 200ms debounce |
| 用户正在行内编辑资料时被刷新覆盖 | `InfoRow` 已只在 `!editing` 时同步 draft（`user-menu.tsx:277-280`），无需改动 |
| 空库/空月份看起来像「坏了」 | 空状态文案 + Mock 徽标 + 写入引导 |

---

## 7. 评审结论（已拍板）

1. **执行范围**：A 组必做全做。
2. **D4 抽屉实现**：Radix Dialog。
3. **B 组取舍**：做 B2（抽屉内删除）、B3（流水按钮红点）、B4（KPI 从上个值起跳）；**B1（mock 补本月样例）不做**。
4. **入口位置**：只放顶栏。

---

## 8. 落地记录

已按 A 组 + B2/B3/B4 实施：

| 文件 | 状态 |
|---|---|
| `frontend/src/lib/events.ts` | 新增（事件契约 + `hasTransactionWrite`） |
| `frontend/src/hooks/useChatTurnCompleted.ts` | 新增（ref 持有 handler，订阅只建一次） |
| `frontend/src/hooks/useChat.ts` | `finally` 广播 `chat-turn-completed`（带 `toolNames`） |
| `frontend/src/lib/api.ts` | 新增 `refetchDashboard` / `refetchBasicInfo` 严格版；`getTransactions` 的 mock 按月份过滤 |
| `frontend/src/components/dashboard/dashboard.tsx` | 订阅并静默重拉 |
| `frontend/src/components/user-menu.tsx` | 订阅并静默重拉 |
| `frontend/src/components/transactions/transactions-drawer.tsx` | 新增（非模态侧栏 + 月份导航 + 汇总 + 删除 + 空/错/Mock 态） |
| `frontend/src/components/top-bar.tsx` | 「流水」入口 + 新数据红点 |
| `frontend/src/components/app-shell.tsx` | 开关状态、抽屉停靠边、红点订阅 |
| `frontend/src/components/chat/chat-panel.tsx` | 空状态补写入引导（置首） |
| `frontend/src/components/dashboard/kpi-overview.tsx` | `CountUp` 从上个值起跳 |

**校验结果**：
- `tsc -p tsconfig.app.json --noEmit` → **0 error**
- `oxlint src` → **0 error / 9 warning**（8 条为既有；新增 1 条为 `transactions-drawer.tsx` 的 `set-state-in-effect`，与既有 `dashboard.tsx:28`、`useChat.ts:116` 同类）
- `vite build` **未能运行**：本沙箱禁止子进程管道（`spawn EPERM`），与代码无关；需在本地 `npm run dev` 目视验收 §0
- 顺带说明：`tsc -b` 会往 `frontend/node_modules/.tmp/` 写 tsbuildinfo，沙箱对该目录只读，故改用等价的 `tsc -p ... --noEmit`

**已知遗留**（B1 未做带来的可见后果）：
Mock 模式下 `mockTransactions` 只有 2026-09 的数据，而抽屉默认月是 2026-10 → 离线演示时抽屉默认是空状态（有 Mock 徽标与写入引导）。
要补的话只需在 `mock-data.ts` 加 3 条本月样例。

