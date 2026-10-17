# RAG 测试框架 · 问题与解决方案

> **模块定位**：`backend/tests/rag/` 的测试与自检。覆盖标准库 runner、
> 单元/契约/端到端分层、以及 `RAG_/eval/selftest_calibration.py`。
>
> **本文件是拆分自 [`问题与解决方案.md`](./问题与解决方案.md) 的模块文档**，
> 索引与跨模块经验见 [`README.md`](./README.md)。
>
> **本模块问题的特殊性**：这一批问题**全部来自实际执行测试后暴露**，
> 没有一条是靠阅读代码发现的。而且它们主要是"测试自身不可靠"——
> 断言方向写反、夹具路径不一致、门槛用例没真正触发 —— 而非被测代码有 bug。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 子模块 |
| :--- | :--- | :--- | :--- | :--- |
| P2-19 | 阈值断言方向写反：保守选项被写成更激进 | 🟠 中高 | ✅ 已修复（有实测证据：自检第 [2] 组已全通过） | 测试 |
| P2-20 | 测试夹具写入的文件名与函数读取路径不一致 | 🟠 中高 | ✅ 已修复（有实测证据：派生 5 项已全通过 + 新增错配报错守卫） | 夹具 |
| P2-21 | 覆盖率门槛用例的分母没真正制造出低覆盖率 | 🟡 中 | ✅ 已修复（有实测证据：低覆盖率用例已正确报错） | 测试 |
| P2-22 | 测试模块的失败清单从不清空 | 🟡 中 | ✅ 已修复（代码，未实测——`main()` 已加 `_FAILURES.clear()`） | 测试 |
| P2-23 | 富文本输出绕过缓冲，导致执行顺序不可读 | 🔵 低 | ✅ 已修复（代码，未实测——自检与 runner 两处均已加 flush） | 可观测性 |
| P2-24 | 工具项已实现但未接线，能力静默缺失 | 🟡 中 | 💡 建议未实施（`recall_w` 仍未接线；与 P1-9 是同一问题，见该卡片） | 评估 |

---

### P2-19 · 阈值断言方向写反：保守选项被写成更激进

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 执行 `eval/selftest_calibration.py`，第 [2] 组报 FAIL |
| **严重度** | 🟠 中高（方向性错误，会让人选到反的阈值） |
| **状态** | ✅ 已修复（有实测证据：自检第 [2] 组已全通过） |
| **所属环节** | 测试 / 阈值判定 |
| **代码位置** | `RAG_/eval/selftest_calibration.py`、`RAG_/src/wealth_rag/thresholds.py` |

#### 🔍 现象

自检报出：

```
[FAIL] 重叠场景保守阈值更偏向拒答
```

而实测值是 `th_con=0.05`、`th=0.50`——**保守值远低于主值**，与断言要求的 `th_con ≥ th` 相反。

#### 🧪 证据

断言原文（错误方向）：

```python
check_true(
    "重叠场景保守阈值更偏向拒答",
    th2_con is not None and th2 is not None and th2_con >= th2,   # ← 方向反了
)
```

而实际上**设计上就不可能是 `≥`**：

| 项 | 语义 | 与另一项的关系 |
| :--- | :--- | :--- |
| 主阈值（分位规则） | positive 顶分的 `1−min_cover` 分位 | 允许 `1−min_cover` 的误拒 |
| 保守备选（网格扫描） | 覆盖率 `≥ min_cover` 约束内的最大阈值 | **恰好就是那个分位点** |

因为分位规则**本身**已经恰好落在覆盖率约束的边界上，网格扫描能取到的最大可行点
不可能超过它。所以 `保守 ≤ 主` 是由构造保证的——**`≥` 是个不可能成立的条件，
断言写反了，而不是代码算错了**。

**另一处真实风险**（顺带发现）：网格点可能正好落在 `max(positive 顶分)` 上。
那意味着"所有 positive 的顶分都 `< 阈值`" → **全部被拒答**。这个边界原先没有兜底。

#### 🎯 根因

**把"我以为的语义"直接写成断言，没有先验证这个语义在构造上是否可能成立。**

两处认知偏差叠加：

1. 直觉上"保守 = 更倾向拒答"，但这里的保守指的是**更少误拒**（保留更多 positive），
   `1−min_cover` 分位规则已经把这个额度用满了，所以没有"更保守"的空间；
2. 断言方向一旦写反，它测的就不是"行为是否正确"，而是"行为是否恰好等于我的误判"。

#### ✅ 解决方案

**(a) 修正断言方向并明确不变量**（写在注释里，避免下次又写反）：

```python
# 不变量（注意方向，早期版本这里写反了）：
#   保守阈值 ≤ 主阈值 ≤ max(positive 顶分)
check_true(
    "可分场景：保守阈值不高于主阈值",
    th_con is not None and th is not None and th_con <= th + 1e-9,
    f"th_con={th_con} th={th}",
)
```

**(b) 补边界兜底**（`thresholds.py`）：

```python
if th_conservative is not None:
    # 网格点可能正好落在 max(positive) 上 → 全部被拒答。
    # 用下一个浮点表示数做上界，确保至少保留一条 positive。
    upper = math.nextafter(max(pos_scores), -math.inf)
    th_conservative = min(th_conservative, upper)
```

**(c) 把不变量提升为独立用例**（`tests/rag/test_thresholds.py::check_threshold_conservative_not_more_aggressive`），
覆盖三种边界：完全重叠 / 单点 positive / 完全可分。

#### ✔️ 验证

```powershell
python backend\RAG_\eval\selftest_calibration.py       # 第 [2] 组应全 ok
python backend\tests\rag\run_tests.py -k threshold     # 不变量用例应通过
```

#### 💡 学习要点

1. **"显然成立"正是要验证的方向。** 当一个断言在你看来不可能失败时，
   它失败往往意味着**你的语义理解错了**，而不是代码错了。这类断言写了等于没写，
   更有害的是它会固化错误认知。
2. **方向性错误的危害大于数值错误。** 数值误差是量的问题；方向反了会让人在
   "选保守方案"时拿到更激进的配置——决策被反向执行。
3. **不变量要写在代码里，不只在文档里。** 这条 `保守 ≤ 主 ≤ max(pos)` 现在同时
   出现在实现注释、自检、单测三处，比写在设计文档里可靠得多。

---

### P2-20 · 测试夹具写入的文件名与函数读取路径不一致

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 执行自检，4 项 FAIL 集中在 `--seed-from-gold` 与派生统计 |
| **严重度** | 🟠 中高（测试假失败，且掩盖了一个真实的静默失效） |
| **状态** | ✅ 已修复（有实测证据：派生 5 项已全通过 + 新增错配报错守卫） |
| **所属环节** | 测试 / 评估标注流程 |
| **代码位置** | `RAG_/eval/selftest_calibration.py`、`RAG_/eval/apply_grades.py` |

#### 🔍 现象

4 项断言同时失败：

```
[FAIL] 派生：gold 候选记为 grade=2: got=0 expected=1
[FAIL] 派生：非 gold 记为 grade=0: got=3 expected=1
[FAIL] 派生：统计到 derived 数量: got=3 expected=2
[FAIL] 派生：gold_headings 只含 gold 项: got=[] expected=['H1']
```

`derived=3` 与"期望 2 个候选"矛盾，说明**函数读到的根本不是测试刚写的那个池**。

#### 🧪 证据

函数签名把"池目录"与"标签文件"分开传入：

```python
def merge_grades(pool_dir, labels_path, out_path, ...):
    pool = [json.loads(line) for line in
            (pool_dir / "pool.jsonl").read_text(...).splitlines() ...]   # ← 固定读 pool.jsonl
```

而测试写的是别的文件名：

```python
(td / "pool2.jsonl").write_text(...)        # ← 写 pool2.jsonl
stats2 = merge_grades(td, td / "seed.json", out2, seed_from_gold=True)
#        ↑ 只传目录，函数去读 pool.jsonl（上一个用例留下的旧池：c1/c2/c3）
```

于是标签里是 `g1/g2`、读到的池是 `c1/c2/c3`，**两者零交集** → 所有 grade 取默认 0。
输出里的两行 `标签里出现候选池之外的 chunk_id 2 个：q1/g1, q1/g2` 正是这个证据，
但当时它只是一条黄色提示，没有阻断流程。

#### 🎯 根因

**两件事同时成立才导致这个 bug：**

1. **接口不一致**：`merge_grades` 把池文件名**硬编码**为 `pool.jsonl`，却把目录作为参数。
   调用方很容易以为"传目录 + 文件名前缀"就能指定池，实际上不行。
2. **静默降级**：候选池与标签不匹配时，函数只是把多余的 chunk_id 记进
   `unknown_chunk_ids` 并在报告里提示，然后继续产出结果。**这又是一次"静默失效"**
   （与 P1-7 / P2-12 同族）——产出了一份看起来正常、实际整题无标注的数据。

#### ✅ 解决方案

**(a) 把静默降级改成显式错误**（这是关键修复）：

```python
pool_ids = {c["chunk_id"] for c in entry["candidates"]}
label_cands = lab.get("candidates") or {}
if pool_ids and not (pool_ids & set(label_cands)):
    raise SystemExit(
        f"{qid}: 标签文件与本查询的候选池完全不匹配"
        f"（标签里有 {len(label_cands)} 个 chunk_id，池里有 {len(pool_ids)} 个，无一交集）。\n"
        f"  最可能的原因：labels.json 不是由当前的 {pool_dir / 'pool.jsonl'} 生成的...\n"
        "  请重新生成候选池与模板：..."
    )
```

**(b) 测试改为覆盖同名文件**，并在测试里写明这个约束：

```python
# 注意：merge_grades 固定读 pool_dir / "pool.jsonl"，因此**必须覆盖同名文件**。
# （早期版本写成 pool2.jsonl，函数根本读不到，于是测的是旧的 3 候选池。）
(td / "pool.jsonl").write_text(...)
```

**(c) 补一条专门的错配用例**，锁住这个错误行为。

#### ✔️ 验证

```powershell
python backend\RAG_\eval\selftest_calibration.py
# 期望：派生 5 项全 ok；「候选池与标签不匹配正确报错」ok
```

#### 💡 学习要点

1. **测试夹具与函数读取路径必须对齐，最好由函数显式接受文件路径而不是目录。**
   "传目录 + 隐式文件名"这种接口是 bug 温床——调用方无法从签名看出实际读哪个文件。
2. **不匹配要报错，不要提示后继续。** 当两份数据"应该对应但零交集"时，
   继续执行产出的结果一定是垃圾；静默产出比直接失败危害大得多（P1-7 同源）。
3. **测试失败时要先怀疑夹具。** 4 项 FAIL 看起来像"功能坏了"，
   实际是"测试没测到想测的东西"——先确认夹具真的把数据送到了被测代码。

---

### P2-21 · 覆盖率门槛用例的分母没真正制造出低覆盖率

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 执行自检，低覆盖率用例报 FAIL |
| **严重度** | 🟡 中（门槛校验形同虚设） |
| **状态** | ✅ 已修复（有实测证据：低覆盖率用例已正确报错） |
| **所属环节** | 测试 |
| **代码位置** | `RAG_/eval/selftest_calibration.py` |

#### 🔍 现象

```
[FAIL] 低覆盖率应报错 未抛出 SystemExit
```

而期望是"覆盖率过低 → 抛错"。

#### 🧪 证据

覆盖率定义与门槛：

```python
coverage = stats["reviewed"] / stats["candidates"]
MIN_COVERAGE = 0.3     # 低于此值直接报错
```

用例构造：池里有 **3** 个候选，只确认 **1** 个 →

```
coverage = 1 / 3 = 33.3%   > 30%    →  不报错（只告警）
```

**用例根本没制造出"低覆盖率"这个前提。** 断言本身是错的（它要求一个不该发生的行为）。

#### 🎯 根因

**写断言时只想了"期望的行为"，没有验算"构造的输入是否真的落在那条分支上"。**

`1/3 = 33.3%` 与门槛 `30%` 只差 3.3 个百分点，肉眼很容易滑过去——
但它决定了这条用例是在测"门槛生效"还是在测"一切都好"。

#### ✅ 解决方案

**扩大分母**，让覆盖率明确低于门槛（10 个候选确认 1 个 = 10%），
并把"为什么必须扩大分母"写进注释：

```python
# 覆盖率 = 被确认的候选数 / 候选总数，MIN_COVERAGE = 0.3。
# 池里有 3 个候选、只确认 1 个 → 33.3%，仍在门槛之上。
# 所以必须**扩大分母**才能真正低于门槛
# （早期版本用 3 个候选，恰好卡在门槛之上，断言永远失败）。
pool_big = [... {"chunk_id": f"b{i}"} for i in range(10) ...]   # 10 个候选
low = {"q1": {"reviewed": ["b0"], "candidates": {"b0": {"grade": 2}}}}   # 确认 1 个 → 10%
```

#### ✔️ 验证

```powershell
python backend\RAG_\eval\selftest_calibration.py    # 「低覆盖率正确报错」应 ok
```

#### 💡 学习要点

1. **门槛类断言必须验算输入与门槛的距离。** "低覆盖率"这种定性描述要换算成具体数字，
   确认它确实落在目标分支上。
2. **边界附近的用例最容易假通过/假失败。** 33.3% vs 30% 只差一个候选的差别——
   这类用例要么远离门槛，要么**显式测边界两侧**（29% 报错 / 31% 通过）。
3. **断言失败时先问"我的输入真的触发了这条分支吗"**，再怀疑被测代码。

---

### P2-22 · 测试模块的失败清单从不清空

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 设计新测试套件时，计划复用 `eval/selftest_calibration.py` 的 `main()`，审阅其实现后发现 |
| **严重度** | 🟡 中（同一进程内重复调用会累积上一次的失败） |
| **状态** | ✅ 已修复（代码，未实测——`main()` 已加 `_FAILURES.clear()`） |
| **所属环节** | 测试 |
| **代码位置** | `RAG_/eval/selftest_calibration.py` |

#### 🔍 现象

失败清单是**模块级**变量，`main()` 从不清空：

```python
_FAILURES: list[str] = []

def main() -> int:
    test_percentile()
    test_choose_threshold()
    ...
    if _FAILURES:      # ← 累积了本进程内所有历史调用的失败
        return 1
    return 0
```

#### 🧪 证据

作为独立脚本运行时没问题（进程只调一次 `main()`）。
但一旦被**同一进程内多次调用**——例如被 `tests/rag` 的测试套件 import 后执行，
或未来加一个"重跑失败用例"的功能——第二次调用的结果会被第一次的失败污染：

```
第 1 次调用：0 项失败 → return 0
第 2 次调用：0 项失败，但 _FAILURES 里还留着…   ← 实际不会有失败残留（通过时不 append）
```

更准确的风险是：**若第 1 次有失败、第 2 次全通过，第 2 次仍会返回 1**（误报失败）。

#### 🎯 根因

**把"单次运行的汇总状态"存在了模块级，而不是调用级。**

`_FAILURES` 的语义是"本次运行的失败清单"，但它的**生命周期**是模块（进程）级。
两者不匹配，就会出现"上一次的结论影响下一次"。

#### ✅ 解决方案

在 `main()` 开头清空：

```python
def main() -> int:
    # 清空失败列表：本模块的 _FAILURES 是模块级的，若在同一进程内多次调用 main()
    # （例如被 tests/rag_tools 的测试套件导入执行），上一次的失败会被重复计入。
    _FAILURES.clear()
    ...
```

#### ✔️ 验证

在 `tests/rag/test_calibration_eval.py::check_eval_selftest_calibration_passes`
里调用 `mod.main()` 并要求返回 0；若不清空，该用例在套件重复运行时会出现不一致。

#### 💡 学习要点

1. **汇总状态的粒度要匹配它的语义。** "本次运行的失败数"应该属于"本次运行"，
   而不是模块。模块级可变状态在"被重复调用"的场景下必然出问题。
2. **测试脚本被当作库复用时，要重新审阅它的状态管理。** 独立脚本的隐式假设
   （"一个进程只跑一次"）在复用时会失效。

---

### P2-23 · 富文本输出绕过缓冲，导致执行顺序不可读

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 观察自检输出：`[6]` 出现在 `[5]` 之前 |
| **严重度** | 🔵 低（只影响可读性，不影响正确性） |
| **状态** | ✅ 已修复（代码，未实测——自检与 runner 两处均已加 flush） |
| **所属环节** | 测试 / 可观测性 |
| **代码位置** | `RAG_/eval/selftest_calibration.py`、`backend/tests/rag/run_tests.py` |

#### 🔍 现象

自检输出里小标题顺序是：

```
[4] 分级 nDCG
...
[6] QueryCase 分级标注解析     ← 6 跑到 5 前面
...
[5] 标注合并与覆盖率校验
```

#### 🧪 证据

`main()` 的调用顺序是 `... test_graded_ndcg → test_query_case_grades → test_merge_grades`，
与输出顺序不符。原因在 `test_merge_grades` 内部：

```python
def _print_report(stats, out_path, coverage):
    from rich.console import Console
    console = Console()          # ← rich 直接写自己的 stdout 句柄
    console.print(hist)          #    绕过 print 的行缓冲
```

`print` 输出经由 `sys.stdout` 缓冲，而 `rich.Console` 直接写底层流。
两者不共享缓冲 → **输出的实际落地顺序由缓冲刷新时机决定**，而非调用顺序。

#### 🎯 根因

**同一进程里两套输出通道，各管各的缓冲。**

这类问题在"混用 print 与 rich / logging / tqdm"时很常见。它不影响正确性，
但会让日志顺序与实际执行顺序不一致——排查问题时会被误导（"为什么 6 先跑完了？"）。

#### ✅ 解决方案

**(a) 在子测试边界 flush**，让顺序符合直觉：

```python
def _run(label: str, fn) -> None:
    """跑一个子测试，保证输出顺序可读。

    被测函数会直接往 stdout 写字（merge_grades 内部的 rich 表格绕过了 print 的
    缓冲），不 flush 的话表格会挤到下一个子测试的标题之前。
    """
    sys.stdout.flush()
    fn()
    sys.stdout.flush()

# main()
_run("[1]", test_percentile)
_run("[2]", test_choose_threshold)
...
```

**(b) 测试 runner 同样处理**（`run_tests.py`）：在每个用例前后 flush，
避免用例内部的 rich 表格挤到用例结论之间。

#### ✔️ 验证

```powershell
python backend\RAG_\eval\selftest_calibration.py
# 期望：[1] → [2] → ... → [6] 顺序单调递增
```

#### 💡 学习要点

1. **混用多套输出通道时要显式管顺序。** print / rich / logging / tqdm 各有缓冲，
   不 flush 就无法保证落地顺序。
2. **顺序错乱的日志会误导排查。** 它不改正确性，但会让人对"哪一步先发生"产生错误判断，
   进而在错误的假设上找 bug。
3. **低严重度不等于不值得修。** 这条的修复成本是 4 行，收益是日志可信。

---

### P2-24 · 工具项已实现但未接线，能力静默缺失

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 审阅 `evaluation.py` 时发现 `recall_w` 字段存在、渲染分支存在，但从未被填充 |
| **严重度** | 🟡 中（会导致把"召回不行"误读为"重排没用"） |
| **状态** | 💡 建议未实施（`recall_w` 仍未接线；与 P1-9 是同一问题，见该卡片） |
| **所属环节** | 评估 |
| **代码位置** | `RAG_/src/wealth_rag/evaluation.py`、`cli.py` |

#### 🔍 现象

`evaluation.py` 已实现 `ceilling_recall(w)` 并写了详细注释解释它的价值：

> 若报告窗口 k 等于重排深度 W，则"前 k 名"恒等于重排输入集，Recall@k 对重排**在数学上完全不敏感**。

但 `main()` 里虽有 `report.recall_w = int(...fused_top...)`，
**`cli.py` 的 `eval_cmd` 与 `eval/run_eval.py` 两条路径都没有设置它**，
因此该指标**从未在任何评估输出里出现过**。

#### 🧪 证据

```python
# evaluation.py::main（已设置）
report.recall_w = int(getattr(settings.retrieval, "fused_top", 0) or 0)

# cli.py::eval_cmd（未设置）
report = evaluate(settings, queries, top_k=top_k, use_rerank=not no_rerank)
render_report(report)     # → render 里 `if report.recall_w:` 为假，整段静默跳过
```

`render_report` 用 `if report.recall_w:` 保护，缺值时**不打印、不报错、不告警**——
典型的"静默消失"。

#### 🎯 根因

**同一能力有多个入口（库 / CLI / 脚本），接线只做了一处。**

`evaluate()` 自身不设置 `recall_w`（它拿不到 `fused_top`），于是每个调用方都要自己记得设置。
只要有一个入口忘了，指标就静默消失——而**"忘记设置"与"配置为 0"在代码里长得一样**。

#### ✅ 解决方案

**建议方案（在 P1-9 已记录，本阶段确认未实施）**：把填充**下沉到 `evaluate()` 内部**，
让调用方无需关心：

```python
def evaluate(settings, queries_path, top_k=10, use_rerank=True, retriever=None):
    ...
    report = EvalReport(rows=rows, top_k=top_k, use_rerank=use_rerank)
    report.recall_w = int(getattr(settings.retrieval, "fused_top", 0) or 0)   # ← 内部填充
    return report
```

并让渲染层区分"未启用"与"未设置"：

```python
if report.recall_w:
    console.print(...)
else:
    console.print("[yellow]⚠️ recall_w 未设置，Recall@W 上界不可用[/yellow]")
```

#### ✔️ 验证

```powershell
python backend\RAG_\eval\run_eval.py --queries backend\RAG_\eval\queries.jsonl --top-k 10
# 期望输出包含：回召率上限 Recall@20（第一阶段候选深度 W） = ...
```

#### 💡 学习要点

1. **"已实现"不等于"已生效"。** 有字段、有注释、有渲染分支，但缺了最后一步赋值——
   这类"接线缺失"不会被任何检查发现，因为每一部分单独看都是对的。
2. **可选输出分支要区分"未启用"与"未设置"。** `if report.recall_w:` 把两者混为一谈，
   前者合法、后者是 bug。
3. **这是 P1-9 的复现**：同一问题在"第二阶段发现、第三阶段确认仍未修"，
   说明"记录待办"不等于"会去做"——待办需要进任务列表，不能只写在文档里。

---
