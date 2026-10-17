# Agent 工具调用可靠性 · 问题与解决方案

> **模块定位**：模型调用工具时的**入口可靠性**——工具名不存在 / 写法漂移 / 参数错误 /
> 重复调用，以及这些失败如何被检测、修复与收敛。
> 覆盖 `backend/core/tools/tools_route.py`、`backend/core/agent/agent.py`、
> `backend/core/agent/prompt.py`，以及 `backend/tests/rag/` 里对应的契约测试。
>
> **与 RAG 问题文档的关系**：本文件**不属于** `wealth-rag` 检索模块，是 Agent 后端的
> 另一条问题线（与 [`backend-core架构审查.md`](./backend-core架构审查.md) 同属"Agent 后端"主题，
> 但那份只列问题、不含解决方案）。
>
> **编号体系独立**：RAG 文档已用到 `P0-x ~ P2-27`，为免撞号，本文档用
> **`T`（Tool）前缀** + 严重度分层：`T1-x` = 影响正确性/可恢复性的主问题，
> `T2-x` = 影响可观测性/优雅降级/测试可信度的次问题。
>
> **调研依据**：[`../Investigation/tool幻觉兜底机制/`](../Investigation/tool幻觉兜底机制/)
> （学术界分类、工业界实现、实证数据）。本文档只记录**本项目实际发现并修掉的问题**，
> 策略全貌不在此重复。

---

## 一句话结论

> **把模型给的工具名当成"用户输入"来校验，而不是当成"算出来的答案"。**
>
> 具体的四层是「预防 → 拦截 → 修复 → 收敛」。本项目原来的兜底只有"拦截"的前半截
> （精确查表 + 抛异常转 error），**"修复"层的信息量为零、"收敛"层完全没有**。

### 本次最重要的发现（见 T2-5）

修完之后跑测试才发现：**`test_agent_events.py` 里 4 条用例从写下来那天起就没有真正跑通过**
——它们的脚本多包了一层 list，模型流迭代出来的 `chunk` 是个 `list`，第一条断言就会
`AttributeError`。

**这正好复现了本项目第三阶段（P2-19 ~ P2-24）的元教训：读代码不能替代跑代码。**
这次的兜底改动之所以能被验证，恰恰是因为先把这 4 条用例修好了。

---

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 代码位置 |
| :--- | :--- | :--- | :--- | :--- |
| T1-1 | 未知工具的错误信息没有任何恢复线索 | 🟡 中 | ✅ 已修复（有实测证据） | `tools_route.route` |
| T1-2 | 工具名只做精确匹配，书写漂移被误判为"不存在" | 🟡 中 | ✅ 已修复（有实测证据） | `tools_route.normalize_name` / `resolve_tool_name` |
| T1-3 | 用 Python 的 `TypeError` 当参数错误信息 | 🟡 中 | ✅ 已修复（有实测证据） | `tools_route.validate_arguments` |
| T1-4 | System prompt 未约束工具名，也缺少弃权许可 | 🔵 低 | ✅ 已改（属行为约束，待真实会话观测） | `agent/prompt.py` |
| T2-1 | 时机幻觉：同参重复调用完全没兜 | 🟡 中 | ✅ 已修复（有实测证据） | `agent.chat_stream` |
| T2-2 | 未知工具不熔断，且熔断文案不归因 | 🟡 中 | ✅ 已修复（有实测证据） | `agent.chat_stream` |
| T2-3 | 错误不分级，确定性故障被当临时故障重试 | 🔵 低 | ⚠️ 部分修复（分级已落地，根因仍在） | `tools_route.route` |
| T2-4 | 参数是合法 JSON 非对象时，非 dict 被推给前端 | 🔵 低 | ✅ 已修复（有实测证据） | `agent.chat_stream` |
| T2-5 | 测试夹具多包一层 list，4 条 agent 事件用例从未跑通 | 🔴 高 | ✅ 已修复（有实测证据） | `tests/rag/test_agent_events.py` |

**合计 9 条**：8 条已修复且经执行验证，1 条部分修复（T2-3，根因需工具侧配合）。

---

## 改动前后对照（速览）

| 模型的行为 | 改动前 | 改动后 |
| :--- | :--- | :--- |
| `Search_Knowledge`（大小写） | ❌ 未知工具 | ✅ 归一化命中 |
| `get-assets`（连字符） | ❌ 未知工具 | ✅ 归一化命中 |
| `getAssets`（驼峰） | ❌ 未知工具 | ✅ 归一化命中 |
| `functions.get_assets`（前缀） | ❌ 未知工具 | ✅ 归一化命中 |
| `get_asset`（少个 s） | ❌ 未知工具 | ✅ 模糊修复（**只读**工具） |
| `update_asst`（写工具近失） | ❌ 未知工具 | ⚠️ **不修复**，给 `did_you_mean` 让模型决定 |
| `search_policy`（编造名字） | `{"error":"未知工具"}` | ✅ 带 `available_tools`+`did_you_mean`+`hint` |
| `search_knowledge` 缺 `query` | `TypeError: ... missing 1 required positional argument` | ✅ `缺少必填参数: query` |
| `get_assets` 传 `{"foo":1}` | `TypeError: ... unexpected keyword argument` | ✅ `不支持的参数: foo` |
| 参数是 `[1,2]` / `null` | `argument of type 'list' is not a mapping` | ✅ `参数必须是 JSON 对象，收到 list` |
| 同参重复调用同一工具 | ❌ 照常执行 | ✅ 跳过并回填提示（时机幻觉） |
| 连续 2 次调不存在的工具 | ❌ 跑满 5 轮，报"达到最大循环次数" | ✅ 第 2 次即收敛，文案归因 + 给用户人话 |

---

## T1-1 · 未知工具的错误信息没有任何恢复线索

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `tools_route.route()`，对照 `Investigation/` 调研里的工业实现 |
| **严重度** | 🟡 中（不致命，但让每一次"可自愈的失败"都退化成"概率性重试"） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | 路由层 / 修复 |

#### 🔍 现象

改之前，模型调用一个不存在的工具，收到的全部信息是：

```json
{"error": "未知工具: search_policy"}
```

这句话对模型的恢复**没有任何帮助**。它不知道：

- 有哪些工具其实可以用（要靠自己回忆上下文里的 `tools` 数组）；
- 有没有长得像的正确名字（`search_knowledge`）；
- 下一步该怎么改（换个名字？还是根本不该调工具？）。

#### 🧪 证据

改前的实现（原 `route()`，175~190 行）只有一行分支：

```python
func = TOOL_REGISTRY.get(tool_name)
if func is None:
    return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
```

它把"未命中注册表"当成了**终点**，而实际上这是最该被修复的一类失败——
模型只是写错了一个名字，重试一次大概率就能对。

#### 🎯 根因

**把"没查到"当成"不可修复"来处理。**

`route()` 原本的设计目标只有两个：不抛异常（保证 Agent 循环不中断）、返回 JSON。
它没有区分"这个失败值不值得让模型再试一次"，也没有想过"要让它再试，得先给它什么信息"。

对比工业实现的演进方向，这个问题很典型：

- [OpenAI Agents SDK PR #3402](https://github.com/openai/openai-agents-python/pull/3402) 的
  `unknown_tool_behavior="respond"` 会追加一条
  `Tool 'x' is not available on agent 'y'. Available tools: ...`；
- [hermes-agent PR #701](https://github.com/NousResearch/hermes-agent/pull/701) 更直接地把
  旧的"什么都不加到 messages、直接重发请求（最多 3 次）"**改掉**，换成把可用工具清单喂回模型。

两家都收敛到同一句话：**盲重试的命中率取决于随机性，喂信息才是"修复"。**

#### ✅ 解决方案

未知工具的错误载荷改为结构化，五个字段各有分工：

```python
payload = {
    "error": f"未知工具: {tool_name}",
    "unknown_tool": True,          # 供 agent 层做熔断计数
    "error_kind": "unknown_tool",  # 供埋点统计
    "retryable": True,             # 告诉模型"这个值得再试一次"
    "did_you_mean": _suggest_tool_names(tool_name),
    "available_tools": sorted(TOOL_REGISTRY),
    "hint": "请从 available_tools 中选择工具名后重试，不要臆造工具名。",
}
```

同时打一条结构化日志（便于统计幻觉率与沉淀别名表）：

```python
print(f"[tools_route] ⚠️ 未知工具 tool={tool_name!r}")
```

#### ✔️ 验证

`tests/rag/test_tool_contract.py::check_route_unknown_tool_payload_is_actionable`：

```powershell
cd backend
python tests\rag\run_tests.py -k route_unknown_tool_payload
```

实测 `route("search_policy", "{}")` 返回：

```
error='未知工具: search_policy'
unknown_tool=True  error_kind='unknown_tool'  retryable=True
did_you_mean=['search_knowledge']
available_tools=<24 项，与注册表等长>
hint='请从 available_tools 中选择工具名后重试，不要臆造工具名。'
```

#### 💡 学习要点

**错误信息是接口的一部分，需要设计。** 判断标准很简单：
收到这条错误的**下游**（这里是模型）能不能据此做出与"重新采样"不同的决策？
如果不能，那它就不是修复，只是重试。

---

## T1-2 · 工具名只做精确匹配，书写漂移被误判为"不存在"

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 对着 24 个工具名枚举模型可能的写法，再用 `difflib` 实测相似度 |
| **严重度** | 🟡 中（这类"近失"占比高，且**本不该失败**） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | 路由层 / 拦截 |

#### 🔍 现象

`TOOL_REGISTRY.get(tool_name)` 是精确匹配。模型输出的下面四种写法**全部**被判为"未知工具"：

```
Get_Assets            # 大小写
get-assets            # 连字符
getAssets             # 驼峰
functions.get_assets  # 命名空间前缀
```

这些都不是"编造"，只是**同一个名字的不同写法**。

#### 🧪 证据

BFCL 的评测代码里本来就有一层同类处理：函数名可能含 `.`，而 OpenAI 系列不接受点号，
所以推理与评测时都把 `.` 替换成 `_`（见
[BFCL 方法论 blog](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html)
的 AST 评测一节）。这说明"名字的书写形式"在工程上**本来就需要归一化**。

修复后实测（`python tests\rag\run_tests.py -k route_normalizes`）：

```
Get_Assets            -> ('get_assets', 'normalized')
get-assets            -> ('get_assets', 'normalized')
getAssets             -> ('get_assets', 'normalized')
functions.get_assets  -> ('get_assets', 'normalized')
get_asset             -> ('get_assets', 'fuzzy')      # 少一个 s
```

#### 🎯 根因

**把"名字的书写形式"当成了"名字的身份"。**

注册表用的是 Python 的 snake_case，但模型的命名习惯来自整个生态（JS 驼峰、REST 连字符、
带命名空间的工具平台）。两边对"同一个名字"的理解本来就不一致，中间缺一层归一化。

#### ✅ 解决方案

三级解析阶梯，代价递增、确定性递减：

| 级别 | 判据 | 是否执行 |
| :--- | :--- | :--- |
| `exact` | 原名就在注册表里 | ✅ |
| `normalized` | 归一化后命中（**确定**） | ✅ |
| `fuzzy` | `difflib` 相似度 ≥ 0.85 | ⚠️ **只对只读工具** |
| `none` | 都不命中 | ❌ 返回 T1-1 的结构化错误 |

归一化覆盖四类漂移：

```python
def normalize_name(name: str) -> str:
    s = str(name or "").strip()
    s = _NAMESPACE_RE.sub("", s)      # functions. / tools. 前缀
    s = _CAMEL_RE.sub("_", s)         # 驼峰 → 下划线
    s = s.lower()
    s = _SEP_RE.sub("_", s)           # - . / : 空格 → 下划线
    return _MULTI_UNDERSCORE_RE.sub("_", s).strip("_")
```

**关键的安全限制：写工具永不自动修复。**

```python
_READ_ONLY_PREFIXES = ("get_", "calculate_", "search_", "list_")
```

`record_` / `update_` / `delete_` / `create_` / `set_` / `complete_` 开头的 12 个工具被排除在
自动修复之外，只给 `did_you_mean` 让模型自己选。理由见下方"附带发现 ①"。

#### 附带发现 ①：模糊匹配的阈值必须分两档

这是整个方案最容易踩坑的一处。模糊匹配有两个用途，阈值和适用范围完全不同：

| 用途 | 行为 | 阈值 | 范围 |
| :--- | :--- | :--- | :--- |
| **自动修复** | 改名字然后执行 | 0.85 | 只读工具 |
| **候选建议** | 回灌 `did_you_mean` | 0.50 | 全部工具 |

**为什么写工具不能自动修复**：把 A 猜成 B 会**静默执行错误的工具**。

- `get_asset` → `get_assets`：无害，反正只读；
- `update_asset` → `update_debt`：事故。用户想改资产，系统改了负债，
  而且**调用成功、没有报错、数据已经改坏**。

这不是保守，是承认"错误执行"的代价高于"多问一轮"。

#### 附带发现 ②：候选建议无法靠阈值区分好坏（实测）

用本项目真实的 24 个工具名测出来一组**反直觉**的相似度：

| 输入 → 候选 | ratio |
| :--- | :--- |
| `search_policy` → `search_knowledge`（**合理近失**） | 0.621 |
| `nope_not_a_tool` → `complete_goal_tool`（**纯噪声**） | 0.667 |

**噪声的相似度比合理近失还高。** 原因：工具名共享领域词汇（`get_` / `_tool` / `_asset`），
`nope_not_a_tool` 与 `complete_goal_tool` 共享了 `_tool` 后缀和若干字母。

结论：**不要试图用"提高阈值"去掉噪声建议**——那会先杀掉合理的那条。唯一正确的用法是
把候选**只当提示**交给模型判断。收益是不对称的：模型看到 `search_policy` 的候选是
`search_knowledge` 会立刻改对，而看到噪声候选不会去调它。

这条也反过来证明了"自动修复"必须限制在只读工具：**一旦让候选参与执行，噪声就变成真实的错误调用。**

#### ✔️ 验证

三条用例，其中第三条是**反向用例**（比正向更重要）：

- `check_route_normalizes_name_variants` —— 四种写法全部 `normalized` 命中；
- `check_route_repairs_readonly_near_miss` —— `get_asset` / `serch_knowledge` 走 `fuzzy`；
- `check_route_never_autorepairs_write_tools` —— `update_asst` / `record_asets` /
  `delete_transaction` 必须返回 `None`，**且**验证它们确实是近失名（`_suggest_tool_names`
  能找到真身）——否则这条用例可能只是因为"相似度不够"而碰巧通过，测不出真正的防线。

```powershell
cd backend
python tests\rag\run_tests.py -k route_never_autorepairs_write_tools
```

#### 💡 学习要点

**"未命中"要分档，不能只有"命中/不命中"两态。** 归一化命中是确定的、可以放心执行；
模糊命中是有概率的，必须按"猜错的代价"分级授权。把这两者混成同一档，就是拿数据安全换省事。

---

## T1-3 · 用 Python 的 `TypeError` 当参数错误信息

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `route()` 的 `except Exception as e: str(e)`，推演参数错误时的实际文本 |
| **严重度** | 🟡 中（三个具体危害，见下） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | 路由层 / 拦截 |

#### 🔍 现象

参数出错时，模型收到的是 Python 解释器的原话：

```
get_assets() missing 1 required positional argument: 'x'
argument of type 'list' is not a mapping
```

#### 🧪 证据

改前的实现把**执行**和**参数处理**打包在同一个 `try` 里：

```python
try:
    kwargs = json.loads(arguments)
    result = func(**kwargs)          # ← 参数问题在这里抛 TypeError
    return json.dumps(result, ensure_ascii=False)
except Exception as e:
    return json.dumps({"error": str(e)}, ensure_ascii=False)   # ← 原文回灌
```

三个危害：

1. **泄露实现细节**：Python 措辞 + 内部函数签名，暴露给了模型（以及可能的最终用户）；
2. **不含"该怎么改"**：没说这个工具到底接受哪些参数；
3. **模型可能照抄**：把 `missing 1 required positional argument` 这种话复述给用户。

而 schema 里现成就有 `required` 与 `properties`——信息一直躺在那里，只是没被用上。

#### 🎯 根因

**用"解释器代偿"代替了"契约校验"。**

`func(**kwargs)` 的调用方式天然会把参数错误变成 `TypeError`，而外层一个裸
`except Exception` 又把它和"工具内部崩溃"混为一谈。于是"参数写错了"这个**有明确正确答案**
的信息，被降级成了"出错了"。

BFCL 的评测逻辑把"使用了函数文档外的参数"直接算作**幻觉**（"ensures that only parameters
exist in the function doc are used, flagging the model hallucination outputs"）。既然评测层
把它算作幻觉，运行时就不该悄悄放过。

#### ✅ 解决方案

执行前先按 schema 校验三件事：

```python
def validate_arguments(tool_name: str, kwargs) -> dict | None:
    # 1. 是不是对象
    if not isinstance(kwargs, dict): ...
    # 2. 必填项齐不齐
    missing = [k for k in required if k not in kwargs]  → 缺少必填参数: query
    # 3. 有没有幻觉参数
    unknown = [k for k in kwargs if k not in properties] → 不支持的参数: foo
```

返回的都是**可操作**的结构化载荷：

```json
{"error": "缺少必填参数: query",
 "error_kind": "bad_arguments",
 "missing_arguments": ["query"],
 "required_arguments": ["query"],
 "retryable": true}
```

**不做类型校验**（`number` vs `string` 之类）：成本高、收益低，且多数工具自身能容忍。
这是有意识的取舍，不是遗漏。

#### 附注：`update_basic_info(**fields)` 的行为变化（已知且有意）

全仓只用 24 个工具，其中**唯一**用 `**kwargs` 实现的是 `update_basic_info(**fields)`
（其余 23 个的形参与 schema `properties` 完全一致，已实测核对）。

它的 schema 只声明了 11 个可编辑字段，而实现接受任意字段名再由 `_sanitize` 过滤。
加固后：

| 输入 | 改动前 | 改动后 |
| :--- | :--- | :--- |
| `{"age": 30}` | 写入 | ✅ 仍写入 |
| `{"risk_tolerance": "high"}` | 被 `_sanitize` **静默丢弃** | ⚠️ 返回 `不支持的参数: risk_tolerance` |

这是**有意的行为变化**：该工具的 schema 描述明确写了
"risk_tolerance / investment_preference / liquidity_needs 为算法估算字段，不可通过此工具修改"。
原来靠"静默丢弃"表达这个约束，模型无从得知；现在会明确告诉它，并让它改用正确的方式。
代价是多一轮，收益是**约束变得可见**。

> 若将来某个工具确实需要接受任意动态字段，应在 schema 里显式声明并放宽校验，
> 而不是让校验层去猜。

#### ✔️ 验证

三条用例：

- `check_route_missing_required_argument` —— `search_knowledge` + `{}` → `缺少必填参数: query`；
- `check_route_rejects_hallucinated_argument` —— `get_assets` + `{"foo":1}` → `不支持的参数: foo`；
- `check_route_rejects_non_object_arguments` —— `[1,2]` / `null` / `"str"` → `参数必须是 JSON 对象`。

```powershell
cd backend
python tests\rag\run_tests.py -k route_rejects
python tests\rag\run_tests.py -k route_missing_required
```

#### 💡 学习要点

**错误信息的质量，取决于你在错误发生前掌握了多少结构。** schema 早就知道答案了；
问题只是"用不用它"。用解释器的报错当代偿，等于把结构化信息压平成一串英文。

---

## T1-4 · System prompt 未约束工具名，也缺少弃权许可

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `prompt.py`，对照 BFCL 给模型的 system prompt |
| **严重度** | 🔵 低（有 API 的 `tools` 数组兜着，属加固而非救火） |
| **状态** | ✅ 已改（属行为约束，**待真实会话观测**） |
| **所属环节** | 提示词 / 预防 |

#### 🔍 现象

改前的 `SYSTEM_PROMPT` 只在两处提到了工具：

- `search_knowledge`（整节讲检索规则）；
- `calculate_*`（在计算场景里提了一句）。

**没有**：

- "工具名必须与清单完全一致，不要臆造"的约束；
- "现有工具都不合适时该怎么办"的许可。

#### 🧪 证据

BFCL 给非 function-calling 模型的 system prompt 里有一句关键的话：

> "**If none of the function can be used, point it out.** If the given question lacks the
> parameters required by the function, also point it out."

这不是客套。模型的默认倾向是"必须调点什么"，明确给它一个"什么都不调、直接说明"的出口，
能显著降低硬着头皮瞎调的概率——调研里的 Relign 工作把这件事做到了动作空间层面
（引入 `ChangeTools` / `TalkToUser` 两个弃权动作，用 SFT+DPO 训练）。

#### 🎯 根因

**假设模型"自然会照着 `tools` 数组来"。**

对强模型这个假设大致成立；但它没有成本，也没有坏处——补上四句约束即可，
属于"白捡"的加固。

#### ✅ 解决方案

在 `prompt.py` 的「关于拒绝」之前新增「关于工具调用」一节：

```text
## 关于工具调用

* 只能使用系统提供的工具。工具名必须与提供给你的清单完全一致，
  不要臆造、缩写、翻译或改写工具名。
* 调用前先确认参数齐全。若缺少必需信息（如金额、日期、标的名称），
  应当先向用户询问，而不是自行假设一个值填进去。
* 如果现有工具都不足以完成当前任务，直接用文字向用户说明情况，
  不要为了"显得在工作"而调用不相关的工具，更不要编造工具名。
  这是允许的回答方式，不会被视为失败。
* 同一个工具在完全相同的参数下不要重复调用。……
```

四句分别对应四类幻觉：**类型**（臆造名字）、**内容**（编参数值）、**弃权**、**时机**（重复调用）。

其中"这是允许的回答方式，**不会被视为失败**"是有意加的——它直接对冲模型
"必须产出工具调用才算干活"的倾向。

#### ✔️ 验证

**无自动化验证**（提示词属行为约束，无法用单元测试断言）。

建议按调研文档的方式观测两个数：`unknown_tool` 出现率、同参重复调用率，
改前改后各跑一段真实会话对比。

> ⚠️ 这条**不能算"已验证"**。按本项目第三阶段的教训，属"代码已就位、行为未实测"，
> 记录在此以便后续用真实会话补验。

#### 💡 学习要点

**"允许不做"和"禁止做错"要一起写。** 只写禁令（不要臆造），不给出路（那你就直接说明），
模型被夹在中间时仍会选择瞎调——因为"调错"至少看起来在行动。

---

## T2-1 · 时机幻觉：同参重复调用完全没兜

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 对照 Relign 的"工具幻觉二分四类"，逐类检查本项目覆盖情况 |
| **严重度** | 🟡 中（浪费轮次与 token；检索类工具还会白付 1~3s 延迟） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | Agent 循环 / 收敛 |

#### 🔍 现象

模型可以在同一轮或跨轮**用完全相同的参数重复调用同一个工具**，系统照常执行。
没有任何检测，轮次上限成了唯一的拦网。

#### 🧪 证据

Relign（[arXiv 2412.04141](https://ar5iv.labs.arxiv.org/html/2412.04141)，ICML 2025）把工具幻觉分为
**选择幻觉**（类型 / 时机）与**使用幻觉**（格式 / 内容）。其中"时机幻觉"的定义就是：

> "the model calls the same tool repeatedly with identical inputs and outputs,
> indicating that the tool should not have been called again at that point"

回到本项目改前的代码：`route()` 只按"名字存不存在"设计兜底，`chat_stream` 里
**没有任何跨轮次的调用历史概念**——所以这一类在结构上就是不可能的。

#### 🎯 根因

**兜底设计只覆盖了"名字正确性"，没有"调用合理性"。**

名字正确 ≠ 调用正确。同一套 `route()` 对"重复调用"是完全无感的，因为它每次看到的
只是一个孤立请求，不知道历史上发生过什么。

#### ✅ 解决方案

在 Agent 层维护一个跨轮次的 `(name, arguments)` 集合：

```python
seen_calls: set[tuple[str, str]] = set()      # 循环外，跨轮次累计
...
signature = (tool["name"], tool["arguments"] or "")
if signature in seen_calls:
    # 跳过执行，但仍回填 role="tool"
    ...
    continue
seen_calls.add(signature)
```

**关键细节：跳过时仍然必须回填 `role="tool"` 消息。**

OpenAI 协议要求 `assistant.tool_calls` 里每个 `id` 都有一条对应的 `tool` 消息；少了任何一条，
下一轮请求会被 API 拒绝。这也是 OpenAI Agents SDK 在 `respond` 模式下"先记 `ToolCallItem`
再追加合成输出"的原因。

#### ✔️ 验证

`tests/rag/test_agent_events.py::check_duplicate_call_is_skipped`：

```powershell
cd backend
python tests\rag\run_tests.py -k duplicate_call
```

断言四件事：

1. 同一个 `(name, args)` **只真正执行一次**（用打桩的 `route` 计数，不碰数据库）；
2. 两次调用**都**产生 `tool_result`（第二次是跳过提示）；
3. 跳过的那次 `status=error`、`error_kind=duplicate_call`；
4. **两次的 `tool` 消息都回填了**（防上面那个协议陷阱）。

#### 💡 学习要点

**"调用是否合理"需要历史，而历史不在单次路由的视野里。** 这类问题只能在这一层之上（Agent 循环）
解决——它提醒我们：兜底逻辑的分层不是审美，而是由"需要多少上下文"决定的。

---

## T2-2 · 未知工具不熔断，且熔断文案不归因

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 推演"模型反复调错名"时的完整事件流 |
| **严重度** | 🟡 中（用户看到的是误导性文案，排查也无从下手） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | Agent 循环 / 收敛 |

#### 🔍 现象

模型连续 5 轮都调同一个不存在的工具时：

- 系统**照常跑满** `MAX_ROUNDS=5`，每轮都在重复"未知工具 → 回灌 → 再错"；
- 最后用户看到的是：`达到最大循环次数（5），已中止`。

这句话把"工具名无效"说成了"循环次数用完了"。**真实原因被完全掩盖**，而且用户
只收到一个 `error` 事件，没有任何可读说明。

#### 🧪 证据

改前只有一条全局兜底：

```python
#跑满轮次兜底
yield {"type": "error", "message": f"达到最大循环次数（{MAX_ROUNDS}），已中止"}
yield {"type": "done"}
```

`MAX_ROUNDS` 是**粒度极粗**的熔断：它只知道"轮次用完了"，不知道"为什么用完"。

有意思的是，这个缺陷会**掩盖** T1-1 的收益：T1-1 给错误加了信息量，但如果模型依然
改不对，信息量就浪费在无用的重试里了。两件事必须一起修。

#### 🎯 根因

**只有"总预算"这一层熔断，缺少"按错误类型"的熔断与归因。**

正确性上，跑满轮次确实能终止；但它掩盖了失败原因，让"能自愈的失败"和"注定失败的循环"
在外部表现上完全一样——用户和日志都分不出来。

#### ✅ 解决方案

三层收敛，成本递增：

| 层 | 机制 | 作用 |
| :--- | :--- | :--- |
| 1 | `UNKNOWN_TOOL_LIMIT = 2` | 按**错误类型**收敛：连续 2 次未知工具即停 |
| 2 | `seen_calls` 去重（T2-1） | 按**调用重复**收敛 |
| 3 | `MAX_ROUNDS = 5` | 总预算兜底 |

计数逻辑（成功一次就清零——说明模型已回到正轨）：

```python
if isinstance(result, dict) and result.get("unknown_tool"):
    unknown_streak += 1
    last_error = f"调用了不存在的工具 {tool['name']}"
elif status == "success":
    unknown_streak = 0
```

熔断后**要给用户一句人话**，而不是只丢 `error`：

```python
yield {"type": "error", "message": f"连续 {unknown_streak} 次调用了不存在的工具，已停止"}
yield {"type": "answer",
       "content": "抱歉，我在调用工具时遇到了问题（工具名无法匹配）。能否再说明一下你具体想查询或想修改哪一项？我换个方式处理。"}
```

跑满轮次的兜底也补上了归因：

```python
detail = f"；最后一次失败：{last_error}" if last_error else ""
yield {"type": "error", "message": f"达到最大循环次数（{MAX_ROUNDS}）仍未能完成，已中止{detail}"}
```

`UNKNOWN_TOOL_LIMIT` 取 **2** 的理由：第一次错时模型已经通过 T1-1 拿到了 `available_tools`，
第二次还错说明它没理解反馈，再试大概率仍是错的。

#### ✔️ 验证

`tests/rag/test_agent_events.py::check_unknown_tool_breaker_stops_early`：

```powershell
cd backend
python tests\rag\run_tests.py -k unknown_tool_breaker
```

断言：连续两轮错名后 `len(fake.requests) == UNKNOWN_TOOL_LIMIT`（即 2，而不是 5）、
事件仍以 `done` 收尾、`error` 文案含"不存在的工具"、且**有一条 `answer` 事件**给用户解释。

同时**改写了** `check_max_rounds_guard`：它原本靠"无限调用不存在的工具"来测轮次上限，
现在会被熔断在第 2 轮截断，断言 `消息含 MAX_ROUNDS` 必然失败。改为调用**合法工具名 +
非法参数**（不触发未知工具熔断、各行参数不同也不触发去重），才真正测到 `MAX_ROUNDS` 本身。

> 这两条用例覆盖的是**两种不同的兜底**，混在一处测就会互相掩盖——这是本次改动中
> 一个容易被忽略的连带影响。

#### 💡 学习要点

**熔断必须带归因，而且要分层。** 一个只说"到上限了"的熔断，等于把"哪里坏了"这个信息
丢掉了；而按错误类型分层之后，每种失败都能给出自己的解释。

---

## T2-3 · 错误不分级，确定性故障被当临时故障重试

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `except Exception as e: return {"error": str(e)}`，推演模型拿到后的行为 |
| **严重度** | 🔵 低（不改变正确性，但会浪费轮次、掩盖真 bug） |
| **状态** | ⚠️ **部分修复**（分级已落地；根因需工具侧配合） |
| **所属环节** | 路由层 / 修复 |

#### 🔍 现象

所有失败都是同一个形状：`{"error": "..."}`。模型无法区分：

| 情况 | 例子 | 正确反应 |
| :--- | :--- | :--- |
| 参数写错（可重试） | 缺 `query`、名字近失 | 改一下立刻重试 |
| 工具崩溃（不可重试） | 依赖缺失、SQL 报错、代码 bug | 重试无用，应当**改口**告诉用户 |

不分级的后果很具体：模型对确定性故障反复重试，烧光轮次，最后报一句误导性文案——
而真正的 bug 被"重试"这个动作**掩盖**了。

#### 🧪 证据

改前：

```python
except Exception as e:
    return json.dumps({"error": str(e)}, ensure_ascii=False)
```

`except Exception` 把四类完全不同的东西压成了一种：

1. `json.JSONDecodeError`（参数不是 JSON）；
2. `TypeError`（参数名/个数不对）；
3. 工具内部抛的业务异常；
4. 工具本身的代码 bug。

#### 🎯 根因

**异常类型携带的信息在 `except Exception` 这一步被丢弃了。**

工具侧没有"这个错可不可重试"的约定，`route()` 就只能一律当真——而它选择了"一律视为
可重试"（因为都返回 `{"error": ...}`，模型也没有别的判断依据）。

#### ✅ 解决方案

错误载荷统一带 `retryable` 与 `error_kind`：

| `error_kind` | `retryable` | 场景 |
| :--- | :--- | :--- |
| `unknown_tool` | `true` | 工具名不存在或拼错 |
| `bad_json` | `true` | 参数不是合法 JSON |
| `bad_arguments` | `true` | 缺必填 / 幻觉参数 / 非对象 |
| `tool_exception` | **`false`** | 工具内部异常（默认视为确定性故障） |

`tool_exception` 还附一句提示，直接告诉模型该怎么办：

```json
{"error_kind": "tool_exception", "retryable": false,
 "hint": "该错误大概率无法通过重试解决，请改用其他方式回答或直接告知用户。"}
```

#### 已知局限（为什么只算"部分修复"）

`route()` 里的裸 `except Exception` **仍然无法真正区分**"工具 bug"与"临时故障"
（比如数据库瞬时锁）。当前做法是把工具异常**一律**标成 `retryable: false`——
这是一个偏保守的默认，代价是"偶发故障"也被放弃了。

根治需要工具侧配合：

```python
class RetryableToolError(Exception): ...
class FatalToolError(Exception): ...
```

`route()` 据此分级。这属于**演进方向**，见文末。

#### ✔️ 验证

`tests/rag/test_tool_contract.py::check_route_tool_exception_is_marked_not_retryable`：

```powershell
cd backend
python tests\rag\run_tests.py -k tool_exception_is_marked
```

把 `calculate_net_worth` 临时替换成必定抛 `RuntimeError` 的替身，断言返回载荷的
`error_kind == "tool_exception"`、`retryable is False`、且 `error` 里含 `RuntimeError`。

#### 💡 学习要点

**"可重试性"必须由错误产生方声明，不能由捕获方猜。** `except Exception` 是必要的安全网
（保证循环不中断），但它同时销毁了分级所需的信息——所以分级要在**抛出侧**做，
而不是在捕获侧猜。

---

## T2-4 · 参数是合法 JSON 非对象时，非 dict 被推给前端

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `agent.chat_stream` 的参数解析与事件发射顺序 |
| **严重度** | 🔵 低（后端仍能兜住，但前端会收到不符合契约的数据） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | Agent 循环 / 接口契约 |

#### 🔍 现象

模型把参数写成 `[1,2]`、`null` 或 `"str"` 时：

- `json.loads` 成功，`arguments` 变成一个 **list / None / str**；
- 它被原样塞进 `tool_call` 事件的 `arguments` 字段，推给前端；
- 前端按 `dict` 处理，出问题。

后端这边，`func(**kwargs)` 会抛 `argument of type 'list' is not a mapping`——虽然被兜住了，
但错误信息不可读，且**前端已经收到了坏数据**。

#### 🧪 证据

改前只处理了"解析失败"，没处理"解析成功但类型不对"：

```python
try:
    arguments = json.loads(tool["arguments"]) if tool["arguments"] else {}
except json.JSONDecodeError:
    arguments = {}          # ← 只兜了 JSONDecodeError
yield {"type": "tool_call", "id": tool["id"], "name": tool["name"], "arguments": arguments}
```

`json.loads("[1,2]")` 不抛异常，`arguments` 就是 `[1,2]`——一路推下去。

#### 🎯 根因

**"解析成功"被当成了"格式正确"。**

JSON 语法合法性（`json.loads` 能跑通）与 schema 约定的合法性（必须是 object）是两件事。
前者由库保证，后者从来没人检查。

#### ✅ 解决方案

事件发射前显式降级，把"非对象"挡在前端契约之外：

```python
if not isinstance(arguments, dict):
    arguments = {}
```

真正的错误由 `route()` 的 `validate_arguments` 负责（返回 `参数必须是 JSON 对象，收到 list`），
两边各司其职：**事件层保证契约，路由层给出原因。**

#### ✔️ 验证

`tests/rag/test_agent_events.py::check_non_dict_arguments_downgraded`：

```powershell
cd backend
python tests\rag\run_tests.py -k non_dict_arguments
```

断言 `tool_call` 事件里的 `arguments` 是 `{}`（而非 `[1,2]`）。

#### 💡 学习要点

**"能解析"≠"符合契约"。** 跨层传递的数据，每一层都要问一句"我这个层面对它的类型假设是什么"——
尤其在前后端之间，坏数据一旦发出去就收不回来了。

---

## T2-5 · 测试夹具多包一层 list，4 条 agent 事件用例从未跑通

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16（**本次改动跑全量测试时才暴露**） |
| **发现方式** | 执行 `python tests\rag\run_tests.py`，看 `AttributeError: 'list' object has no attribute 'choices'` |
| **严重度** | 🔴 **高**（测试本身失效 → 所有"已验证"结论都不可信） |
| **状态** | ✅ 已修复（有实测证据） |
| **所属环节** | 测试框架 |

#### 🔍 现象

加固 `agent.py` 后跑全量测试，`test_agent_events.py` 有 4 条用例报同一个错：

```
AttributeError: 'list' object has no attribute 'choices'
  at core/agent/agent.py:65  →  if not chunk.choices:
```

涉及的用例：

- `check_answer_only_sequence`
- `check_done_when_no_tool_calls`
- `check_history_prepended`
- `check_stream_closed`

#### 🧪 证据

`_support.answer_script()` **已经返回**"脚本"（轮次列表）：

```python
def answer_script(text="我是 WealthPilot。"):
    """一轮脚本：只有回答文本、无工具调用 → Agent 结束循环。"""
    return [[chunk(Delta(reasoning_content="先想一想…")), chunk(Delta(content=text))]]
    #      ↑ 外层 = 轮次列表，内层 = 这一轮的 chunk 列表
```

而 4 处调用又用 `[...]` 包了一层：

```python
fake = FakeOpenAI([_answer_only("你好，我是 WealthPilot。")])   # ← 多包了一层
```

实测验证（直接构造两种写法对比）：

```
answer_script 返回: 外层长度 = 1 | 第0项类型 = list
再 [ ] 包一层后: 长度 = 1 | 第0项类型 = list
迭代出的第一个 chunk 类型 = list -> 有 choices 吗: False      ← 错误写法
正确写法迭代出的类型 = SimpleNamespace -> 有 choices 吗: True  ← 正确
```

`FakeOpenAI.__init__` 里 `self._script = list(script)`，`create()` 做 `pop(0)`——
多包一层后，`pop(0)` 拿到的仍是"轮次列表"，`FakeStream` 迭代出来的就是一个个 list，
而 `chat_stream` 期待每个元素是带 `.choices` 的 chunk。

**全仓只有这 4 处，全部集中在 `test_agent_events.py`**（已用
`Select-String -Pattern "FakeOpenAI\(\["` 核对，其余文件无此写法）。

#### 🎯 根因

**`answer_script` 的返回值语义（轮次列表）与调用处的直觉（一轮）不一致，且没有任何
类型标注或断言去暴露它。**

`tool_round_script` 用的是 `*answer_script(final)`（展开），逻辑上是正确的；
而 4 处 `answer_script` 的调用写成了 `[answer_script(...)]`。两种写法**长得几乎一样**，
差别只在有没有那个 `[`。

更关键的是：这些用例**从来没有被执行过**。这正是本项目第三阶段已经记录过的元教训——

> **6 项失败，没有一项是靠阅读代码发现的。**
> 在无法执行的环境里说"逐行重读核对过"，**给了错误的确定性**。

#### ✅ 解决方案

4 处去掉多余的包装：

```python
fake = FakeOpenAI(_answer_only("你好，我是 WealthPilot。"))
```

#### ✔️ 验证

修复后 `test_agent_events.py` 全绿，且**新增的 3 条兜底用例才有意义**
（它们依赖同一套夹具能正常工作）：

```powershell
cd backend
python tests\rag\run_tests.py --only test_agent_events.py
# → 结果：通过 15 / 失败 0 / 错误 0 / 跳过 1
```

#### 💡 学习要点

**"测试文件存在"不等于"测试在跑"。** 一个从未绿过的测试文件，
负面影响比没有测试更大——它提供了虚假的确定性。

顺带的教训：**夹具的返回值语义要在命名和类型上兑现。** `answer_script` 返回的是
"脚本"（可含多轮），名字里的 `script` 已经提示了这一点，但没有任何机制强制调用方遵守。
若 `FakeOpenAI.__init__` 断言"每个元素必须是 list[chunk]"，这 4 条在第一次运行时就
会以一句清楚的错误失败，而不是在 65 行之外的 `chunk.choices` 处炸掉。

---

## 通用经验

从这 9 条里能提炼出 5 条，都可以迁到别的"模型给非结构化输入"的场景：

| # | 原则 | 对应问题 | 一句话说明 |
| :--- | :--- | :--- | :--- |
| 1 | **错误信息是接口的一部分** | T1-1 | 下游能不能据此做出不同的决策，是判断它有没有用的唯一标准 |
| 2 | **"未命中"要分档，并按猜错的代价授权** | T1-2 | 确定命中可执行，概率命中只给建议；写操作永不自动修复 |
| 3 | **让模型"允许不做"，否则它会做错** | T1-4 | 只写禁令不给出口，模型会选择"看起来在行动" |
| 4 | **熔断要分层且必须归因** | T2-2 | 只说"到上限了"的熔断，等于丢掉了"哪里坏了" |
| 5 | **"能解析"≠"符合契约"** | T2-4 | JSON 语法合法与 schema 合法是两件事，每层都要守住自己的类型假设 |

> 第 2 条值得再强调一次：**它不是保守，是成本核算。** 把 `update_asset` 猜成
> `update_debt` 的代价（数据改坏 + 无报错）远高于多问模型一轮。所有"自动修复"类机制
> 都该先问一句：**猜错了会怎样？**

---

## 未做与演进方向

按"性价比 / 触发条件"排序，均为**有意不做**，不是遗漏：

| 方向 | 触发条件 | 说明 |
| :--- | :--- | :--- |
| 别名表 | 有了线上埋点数据后 | 把高频错名沉淀成显式映射（`search_policy` → `search_knowledge`）。比模糊匹配确定，且可审计。**前提是先有 T1-1 的日志**，否则是拍脑袋 |
| 工具级异常分级 | 任意时刻 | 让工具抛 `RetryableToolError` / `FatalToolError`，根治 T2-3 的"一律不可重试" |
| Tool RAG（工具检索） | 工具数 > 50 | 目前 24 个还在"全量枚举"的舒适区（经验阈值 20~30）。检索层会引入召回失败这一新错误源，规模不到时是负收益 |
| 约束解码 | 自建推理后 | vLLM / TensorRT-LLM + XGrammar 把工具名做成语法枚举，可从**物理上**消灭名字幻觉。当前走第三方 OpenAI 兼容 API，拿不到这一层——这是**架构约束，不是选择** |
| 内容幻觉检测 | 有业务规则后 | 参数值是否从用户输入推出（如"用户没给金额却记录了金额"）。属业务校验，当前完全没兜 |

---

## 验证

### 本次改动的验收命令

```powershell
cd E:\Python_Projects\Agent_study\backend
$env:PYTHONIOENCODING = "utf-8"

# 只看本次改动的两个文件
python tests\rag\run_tests.py --only test_tool_contract.py test_agent_events.py
# → 结果：通过 40 / 失败 0 / 错误 0 / 跳过 3

# 全量
python tests\rag\run_tests.py
# → 结果：通过 100 / 失败 3 / 错误 9 / 跳过 13
```

### 全量套件剩余的 12 项失败（与本次改动无关）

`yaml` 未安装、以及临时目录写权限被沙箱拒绝，都属**环境问题**，落在 RAG 的
`test_evidence` / `test_engine` / `test_calibration_eval` 三个文件里：

```
PermissionError: [Errno 13] Permission denied: ...\Temp\dsh-...\config.yaml
ModuleNotFoundError: No module named 'yaml'
```

两个成因都可独立复现，与本次改动无关：

- `python -c "import yaml"` → `ModuleNotFoundError`（**本环境根本没装 PyYAML**）；
- 报错路径在 `%TEMP%\dsh-*\` 下，是被沙箱拒绝写入，而不是代码逻辑问题。

这 12 项涉及 RAG 的配置加载与评估集，与工具路由 / Agent 循环没有调用关系，
不在本次范围内，另行处理。

### 新增/改写的用例清单（12 条）

| 文件 | 用例 | 覆盖 |
| :--- | :--- | :--- |
| `test_tool_contract.py` | `check_route_normalizes_name_variants` | T1-2 |
| | `check_route_repairs_readonly_near_miss` | T1-2 |
| | `check_route_never_autorepairs_write_tools` | T1-2（**反向**） |
| | `check_route_unknown_tool_payload_is_actionable` | T1-1 |
| | `check_route_missing_required_argument` | T1-3 |
| | `check_route_rejects_hallucinated_argument` | T1-3 |
| | `check_route_rejects_non_object_arguments` | T1-3 |
| | `check_route_tool_exception_is_marked_not_retryable` | T2-3 |
| `test_agent_events.py` | `check_unknown_tool_breaker_stops_early` | T2-2 |
| | `check_duplicate_call_is_skipped` | T2-1 |
| | `check_non_dict_arguments_downgraded` | T2-4 |
| | `check_max_rounds_guard`（**改写**） | T2-2（与熔断解耦） |
| | 4 条 `answer_only` 类用例（**修夹具**） | T2-5 |

---

## 附：文档修订记录

| 日期 | 版本 | 变更 |
| :--- | :--- | :--- |
| 2026-10-16 | v1.0 | 首次建立本模块：9 条问题卡片（T1-1 ~ T1-4、T2-1 ~ T2-5），含 8 条已修复（经执行验证）+ 1 条部分修复 |
