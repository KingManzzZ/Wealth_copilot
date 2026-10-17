# RAG 融合测试框架

覆盖 `backend/core/tools/tools_hub/`（RAG 工具与适配层）、`backend/core/agent/`（事件流）
以及 `backend/api/routes/chat.py`（SSE 端到端）。

---

## 快速开始

```powershell
cd E:\Python_Projects\Agent_study\backend

python tests\rag\run_tests.py                 # 跑默认套件（不加载模型，秒级）
python tests\rag\run_tests.py --list          # 只列文件
python tests\rag\run_tests.py -k evidence     # 按关键字筛选用例
python tests\rag\run_tests.py --verbose       # 每个用例都打印
python tests\rag\run_tests.py --only test_engine.py
```

启用真实模型测试（会加载约 2.3GB 模型，需已摄入知识库）：

```powershell
$env:RAG_TEST_REAL = "1"
python tests\rag\run_tests.py                 # 全套含真实检索
python tests\rag\run_tests.py --only test_real_rag.py
```

也可用 pytest（需按上文配置 `python_functions = check_*`，否则收集到 0 个用例）：

```powershell
python -m pytest tests\rag -v
```

> **不依赖 pytest。** runner 用标准库实现，保证任何 Python 3.10+ 环境都能跑测试——
> 测试框架本身不该成为"运行测试"的门槛。
>
> ⚠️ **但"pytest 也能用"需要额外配置。** 本目录的用例统一命名为 `check_*`，
> 而 pytest 默认只收集 `test*` 开头的函数，因此 `python -m pytest tests\rag -v`
> 会报告 **collected 0 items**（退出码 5）——它能打开文件，却认为里面没有测试。
> 要用 pytest 跑，需先让它认这个前缀（仓库当前没有 pytest 配置文件）：
>
> ```ini
> # pytest.ini
> [pytest]
> python_files = test_*.py
> python_functions = check_*
> ```
>
> 另需注意：pytest 在收集阶段就会 import 测试模块，因此需真实依赖的套件
> （`test_real_rag.py` / `test_real_agent.py`）在被跳过之前就可能因缺依赖而报错。
> **日常请用 `run_tests.py`。**

---

## 分层设计

测试按**依赖成本**从低到高排列，这样默认套件永远是秒级的：

| 层 | 文件 | 依赖 | 验证什么 |
| :--- | :--- | :--- | :--- |
| 1 · 纯逻辑 | `test_thresholds.py` | 无 | 阈值判定数学、查询分类优先级 |
| 2 · 纯逻辑 | `test_evidence.py` | 无 | 证据适配、三档质量、预算裁剪、信息瘦身 |
| 3 · 引擎 | `test_engine.py` | 无（注入替身） | 异常翻译、参数落地、预热、自检、并发 |
| 4 · 评估口径 | `test_calibration_eval.py` | 无模型 | 校准口径 ↔ 集成口径一致性；纳入 `eval/selftest_calibration.py`；评估集结构校验 |
| 5 · 契约 | `test_tool_contract.py` | tools_route | schema ↔ 注册表一致性 |
| 6 · 事件流 | `test_agent_events.py` | openai（假客户端） | SSE 事件序列、消息回填、轮次上限 |
| 7 · 端到端 | `test_api_e2e.py` | fastapi TestClient（假 LLM） | 真实驱动 `/api/chat` 全链路 |
| 8 · 真实检索 | `test_real_rag.py` | 🔒 torch + 知识库 | 真实模型/索引可用性、延迟画像 |
| 9 · 真实行为 | `test_real_agent.py` | 🔒 LLM API + 知识库 | **触发准确率、改写质量、拒答行为** |

**为什么第 1~3 层能完全不加载模型**：集成层（`_rag_impl.py` / `rag_evidence.py`）刻意
**不 import** `wealth_rag` 与 `torch`，只用 `getattr` 访问检索结果的 7 个属性。
因此测试可注入合成替身。这既是测试友好，也把集成层与检索引擎的耦合面压到了最小。

---

## 各层在防什么问题

每个测试文件都对应 `问题文档/问题与解决方案.md` 里记录过的真实问题：

| 测试 | 防的问题 |
| :--- | :--- |
| `check_payload_no_internal_fields` | **P2-14** UUID / 本地绝对路径 / 内部分数泄露给 LLM |
| `check_classify_no_rerank_is_medium_not_low` | **P0-11** 二值 `low_confidence` 把"不确定"误判成"该拒答" |
| `check_rerank_floor_keeps_gap` | **P0-12** 三档退化成两档（中间档永远走不到） |
| `check_budget_drops_whole_items` | **P2-13** `parent_text` 体积失控；截断单条会产生语义不完整的证据 |
| `check_private_functions_registered` | **P1-10** 工具注册表被辅助函数污染 |
| `check_every_schema_has_implementation` | 静默失效：schema 与实现两份清单不一致 |
| `check_tool_event_order` | **P2-16** 工具执行期间无进度反馈 + 事件序错乱 |
| `check_startup_survives_rag_unavailable` | **P2-15** 可选能力的故障拖垮整个服务 |
| `check_search_out_of_corpus_is_low` | **P0-11 的实测部分**：拒答阈值是否偏低（会硬答库里没有的内容） |

---

## 真实模型测试失败的读法

`test_real_rag.py` / `test_real_agent.py` 的失败**不一定代表代码有 bug**，
很可能是**提示词或阈值需要调**。失败信息里已写明下一步该改什么，速查：

| 失败用例 | 含义 | 下一步 |
| :--- | :--- | :--- |
| `check_search_in_corpus_is_not_low` | 库内有答案却判「无证据」 | `no_answer_threshold` **偏高** → 调低 |
| `check_search_out_of_corpus_is_low` | 库外问题没判「无证据」 | `no_answer_threshold` **偏低** → 调高（会硬答/编造） |
| `check_triggers_on_policy_question` | 该查没查 | 加强工具 description 与 SYSTEM_PROMPT 的「何时必须检索」 |
| `check_no_retrieval_for_user_data` | 不该查却查了 | 在 description 的「不适用」里更明确点出资产/负债/流水/目标 |
| `check_query_is_standalone_on_followup` | 追问未补全指代 | A1（提示词引导）不够 → 上 A2（集成层自动改写） |
| `check_query_includes_user_numbers` | query 里没有用户的具体数值 | 强化「先用数据工具取数值再写进 query」的要求 |
| `check_abstains_when_no_evidence` | **该拒答却编造** | 最高优先级：检查 SYSTEM_PROMPT 拒答规则与 `notice` 传递 |

### 阈值校准的正确流程

不要在测试失败后凭感觉改 `config.yaml`。用 `RAG_/eval/` 下的工具按数据定值：

```powershell
cd E:\Python_Projects\Agent_study\backend\RAG_

# 1) 生成候选池（关闭重排取候选，避免确认偏误）
python eval\build_eval_pool.py --queries eval\queries.jsonl --out eval\pool --pool-depth 10

# 2) 由已有 gold 派生初值（足以校准 no_answer_threshold）
python eval\apply_grades.py --pool eval\pool --labels eval\pool\labels.template.json `
    --out eval\queries.graded.jsonl --seed-from-gold

# 3) 出分布、推荐值与 rerank A/B（配对 bootstrap 检验）
python eval\tune_rerank_threshold.py --queries eval\queries.graded.jsonl --ab --json runs\calib.json
```

详见 [`../../RAG_/eval/build_eval_pool.py`](../../RAG_/eval/build_eval_pool.py) 的模块文档，
以及 `Investigation/04-open-questions-and-eval.md`。

---

## 已知空白（尚未覆盖）

诚实记录，避免误以为"全测过了"：

| 空白 | 原因 | 影响 |
| :--- | :--- | :--- |
| **并发压力测试** | `test_engine.py` 只做 8 线程逻辑并发，未做真实 GPU 并发 | 多用户同时检索时的显存峰值行为未验证 |
| **`uvicorn --workers 1` 约束** | 无法在测试内启动多 worker | Qdrant 文件锁冲突只能在部署时验证（见 P2-17） |
| **追问改写的自动评估** | 评估集格式不读 `history`，检索只收单条 query | 改写质量只能靠 `test_real_agent.py` 的行为测试 |
| **前端 `tool_start` 渲染** | 测试止于 SSE 事件；未覆盖 React 侧 | 事件已产出，前端是否消费需人工确认 |
| **长时运行内存** | 未做 soak test | 模型常驻 + 长会话的显存/内存增长未测量 |

---

## 目录结构

```
backend/tests/rag/
├── run_tests.py            # 标准库 runner（也可被 pytest 收集）
├── conftest.py             # pytest 兼容（把本目录加进 sys.path）
├── _support.py             # 路径引导 + 替身对象（含假 LLM）+ 环境探测
├── README.md               # 本文件
├── test_thresholds.py      # 1 阈值判定纯逻辑
├── test_evidence.py        # 2 证据适配层
├── test_engine.py          # 3 检索引擎（注入替身）
├── test_calibration_eval.py# 4 评估口径一致性
├── test_tool_contract.py   # 5 schema ↔ 注册表契约（+ 导入自包含 / 依赖归一）
├── test_agent_events.py    # 6 Agent SSE 事件流
├── test_api_e2e.py         # 7 /api/chat 端到端
├── test_real_rag.py        # 8 真实检索（🔒 默认跳过）
└── test_real_agent.py      # 9 真实行为（🔒 默认跳过）
```

> `_support.py` **不在 `backend/core/tools/tools_hub/` 下**，因此不会被 `tools_route`
> 的自动发现扫到。这一点是刻意的：测试替身绝不能进工具注册表。
>
> `_support.py` 里的假 LLM（`FakeOpenAI` / `Delta` / `chunk` / `tool_call_delta` /
> `answer_script` / `tool_round_script`）是第 6、7 层共用的**唯一一份**实现——
> 两个文件各自复制一份曾导致改一处漏一处。

---

## 融合落地的改动清单

本测试框架配套的集成改动（都在 `backend/` 下）：

| 文件 | 改动 |
| :--- | :--- |
| `tests/rag/_support.py` | 假 LLM（`FakeOpenAI` / `Delta` / `chunk` / `tool_call_delta` / `answer_script` / `tool_round_script`）收敛到此处，供 `test_agent_events.py` 与 `test_api_e2e.py` 共用 |
| `core/tools/tools_hub/rag_tools.py` | **新建**：唯一对外工具 `search_knowledge` |
| `core/tools/tools_hub/_rag_impl.py` | **新建**：单例引擎、线程锁、预热、自检、证据组装 |
| `core/tools/tools_hub/rag_evidence.py` | **新建**：证据适配层（三档质量、预算裁剪、信息瘦身） |
| `core/tools/tools_schema.py` | 注册 `search_knowledge` 契约（第 7 组） |
| `core/agent/prompt.py` | 补知识库规则：何时检索、查询构造、引用格式、拒答、注入防御 |
| `core/agent/agent.py` | 新增 `tool_start` 进度事件 |
| `main.py` | lifespan 预热（吞异常降级） |
| `RAG_/src/wealth_rag/thresholds.py` | **新建**：阈值判定纯逻辑（可独立测试） |
| `RAG_/src/wealth_rag/evaluation.py` | 采集 rerank 分数 + `grades` 分级标注 + 分级 nDCG |
| `RAG_/eval/build_eval_pool.py` 等 | 阈值校准工具链（见 `RAG_/eval/`） |
