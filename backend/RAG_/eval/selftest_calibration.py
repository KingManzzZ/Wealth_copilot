"""校准逻辑的自检（不加载模型、不需要知识库）。

校准的推荐值会直接写进 ``config.yaml`` 并决定「拒答还是硬答」，所以它的数学必须能独立验证。
本脚本只测纯函数：分位计算、阈值选择规则、查询分类、分级 nDCG、标注覆盖率校验。

**不依赖** torch / qdrant / transformers —— 因此可在任何环境、任何时间跑，
适合放进 CI 或 pre-commit。

用法::

    python eval/selftest_calibration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许在未安装包的情况下直接运行（把 src/ 加进搜索路径）
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_FAILURES: list[str] = []


def check(name: str, got, expected, tol: float = 1e-9) -> None:
    if isinstance(expected, float) and isinstance(got, (int, float)):
        ok = abs(got - expected) <= tol
    else:
        ok = got == expected
    if ok:
        print(f"  [ok]   {name}")
    else:
        print(f"  [FAIL] {name}: got={got!r} expected={expected!r}")
        _FAILURES.append(name)


def check_true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [ok]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        _FAILURES.append(name)


# ======================================================================
def test_percentile() -> None:
    from wealth_rag.calibration import _percentile

    print("\n[1] 分位计算")
    s = [0.0, 1.0, 2.0, 3.0, 4.0]
    check("p0 = 最小值", _percentile(s, 0.0), 0.0)
    check("p100 = 最大值", _percentile(s, 1.0), 4.0)
    check("p50 = 中位数", _percentile(s, 0.5), 2.0)
    check("p25 线性插值", _percentile(s, 0.25), 1.0)
    check("单元素集", _percentile([7.0], 0.3), 7.0)
    import math

    check_true("空集返回 nan", math.isnan(_percentile([], 0.5)))


def test_choose_threshold() -> None:
    from wealth_rag.calibration import choose_no_answer_threshold

    print("\n[2] 阈值选择规则（主规则：positive 顶分的 1−min_cover 分位）")
    # 完全可分的两组：positive 顶分 0.6~1.0，negative 顶分 0.0~0.02
    pos = [0.60 + 0.01 * i for i in range(41)]  # 0.60 … 1.00
    neg = [0.00 + 0.0005 * i for i in range(41)]  # 0.0 … 0.02
    th, th_con, f1p, curve = choose_no_answer_threshold(pos, neg, min_cover=0.95)

    # min_cover=0.95 → 取 positive 的 5% 分位
    check("可分场景：阈值落在 positive 低端", th, 0.62, tol=0.005)
    check_true(
        "可分场景：阈值高于全部 negative",
        th is not None and th > max(neg),
        f"th={th} max(neg)={max(neg)}",
    )
    # 不变量（注意方向，早期版本这里写反了）：
    #   保守阈值 ≤ 主阈值 ≤ max(positive 顶分)
    # 保守值**不应高于**主值——分位规则本身已允许 (1−min_cover) 的误拒，
    # 高于它就意味着"更激进"，与"保守"语义矛盾。
    check_true(
        "可分场景：保守阈值不高于主阈值",
        th_con is not None and th is not None and th_con <= th + 1e-9,
        f"th_con={th_con} th={th}",
    )
    check_true(
        "可分场景：两个阈值都高于全部 negative",
        th_con is not None and th_con > max(neg),
        f"th_con={th_con} max(neg)={max(neg)}",
    )
    check_true("扫阈曲线非空", len(curve) > 0)
    check_true("曲线含 feasible 标记", all("feasible" in c for c in curve))

    # 不可分：两组完全重叠 → 任何阈值都会误拒或漏拒
    overlap_pos = [0.1 * i for i in range(11)]
    overlap_neg = [0.1 * i for i in range(11)]
    th2, th2_con, _, _ = choose_no_answer_threshold(overlap_pos, overlap_neg, 0.95)
    check_true("重叠场景仍给出阈值（不崩）", th2 is not None)
    # 正确的不变量：分位规则允许 (1−min_cover) 的误拒，因此保守选项（覆盖率约束内的
    # 最大阈值）**不应高于**主值——高于就意味着它反而更激进，与"保守"语义矛盾。
    # （早期版本的断言写反了，见 selftest 的修订记录。）
    check_true(
        "重叠场景保守阈值不高于主阈值",
        th2_con is not None and th2 is not None and th2_con <= th2 + 1e-9,
        f"th_con={th2_con} th={th2}",
    )
    check_true(
        "保守阈值仍保留至少一条 positive",
        th2_con is not None and th2_con < max(overlap_pos),
        f"th_con={th2_con} max(pos)={max(overlap_pos)}",
    )

    # 缺 positive → 无法定阈值
    th3, _, _, _ = choose_no_answer_threshold([], neg, 0.95)
    check("无 positive：返回 None", th3, None)

    # 缺 negative → 仍能给出阈值（但无拒答依据）
    th4, _, _, _ = choose_no_answer_threshold(pos, [], 0.95)
    check_true("无 negative：仍给出阈值", th4 is not None)


def test_classify() -> None:
    from wealth_rag.calibration import classify_query

    print("\n[3] 查询归类")

    class Case:
        def __init__(self, category, grades):
            self.category = category
            self.grades = grades

    check("含 grade=2 → positive", classify_query(Case("policy", {"a": 2})), "positive")
    check("仅 grade=1 → partial", classify_query(Case("policy", {"a": 1})), "partial")
    check("全 0 → negative", classify_query(Case("policy", {"a": 0, "b": 0})), "negative")
    check(
        "「负例」前缀优先 → negative",
        classify_query(Case("负例-荐股", {"a": 2})),
        "negative",
    )
    check(
        "grade=2 与 1 混合 → positive",
        classify_query(Case("fact", {"a": 1, "b": 2})),
        "positive",
    )


def test_graded_ndcg() -> None:
    import math

    from wealth_rag.evaluation import _GAIN, _graded_ndcg

    print("\n[4] 分级 nDCG")

    class Res:
        def __init__(self, cid):
            self.chunk_id = cid

    class Case:
        def __init__(self, grades):
            self.grades = grades

        def grade_of(self, cid):
            return int(self.grades.get(cid, 0))

    check("增益映射 grade2=3", _GAIN[2], 3.0)
    check("增益映射 grade1=1", _GAIN[1], 1.0)
    check("增益映射 grade0=0", _GAIN[0], 0.0)

    # 完美排序：grade2 在前，grade1 其次，无关在后 → nDCG = 1
    grades = {"g2": 2, "g1": 1, "g0": 0}
    perfect = [Res("g2"), Res("g1"), Res("g0")]
    check("完美排序 nDCG=1", _graded_ndcg(perfect, Case(grades), 3), 1.0, tol=1e-9)

    # 最差排序：无关在前，grade1 次之，grade2 最后 → nDCG < 1
    worst = [Res("g0"), Res("g1"), Res("g2")]
    v = _graded_ndcg(worst, Case(grades), 3)
    check_true("最差排序 nDCG<1", v < 1.0, f"got={v}")
    check_true("最差排序 nDCG>0", v > 0.0, f"got={v}")

    # 全部漏掉（返回的全是 grade=0）→ DCG 为 0
    allmiss = [Res("g0"), Res("gx"), Res("gy")]
    check("全漏 nDCG=0", _graded_ndcg(allmiss, Case(grades), 3), 0.0)

    # IDCG 必须按「标注里的最高增益」算，而不是「返回结果里的最高增益」
    only_g1 = [Res("g1")]
    v2 = _graded_ndcg(only_g1, Case(grades), 1)
    check_true(
        "只返回 grade1 时 nDCG<1（分母含未召回的 grade2）",
        v2 < 1.0,
        f"got={v2}",
    )
    expected = (1.0 / math.log2(2)) / (3.0 / math.log2(2))
    check("只返回 grade1 的 nDCG = 1/3", v2, expected, tol=1e-9)


def test_merge_grades() -> None:
    print("\n[5] 标注合并与覆盖率校验")
    import importlib.util
    import json
    import tempfile

    # eval/ 不是包（无 __init__.py），按文件路径加载最稳
    spec = importlib.util.spec_from_file_location(
        "_apply_grades", Path(__file__).resolve().parent / "apply_grades.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    merge_grades = mod.merge_grades

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pool = [
            {
                "id": "q1",
                "query": "查询一",
                "category": "policy",
                "answerable": True,
                "note": "",
                "candidates": [
                    {"chunk_id": "c1", "heading_path": ["第三章", "养老"]},
                    {"chunk_id": "c2", "heading_path": ["第三章", "养老"]},
                    {"chunk_id": "c3", "heading_path": ["第四章", "税"]},
                ],
            }
        ]
        (td / "pool.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pool) + "\n",
            encoding="utf-8",
        )
        labels = {
            "q1": {
                "reviewed": ["c1", "c2", "c3"],
                "candidates": {
                    "c1": {"grade": 2},
                    "c2": {"grade": 0},
                    "c3": {"grade": 1},
                },
            }
        }
        lpath = td / "labels.json"
        lpath.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        out = td / "graded.jsonl"
        stats = merge_grades(td, lpath, out)

        check("覆盖率 = 100%", stats["coverage"], 1.0, tol=1e-9)
        check("grade 直方图 2/1/0", stats["grade_hist"], {0: 1, 1: 1, 2: 1})
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
        check("输出 1 条查询", len(rows), 1)
        check("grades 透传", rows[0]["grades"]["c1"], 2)
        check_true(
            "gold_headings 由 grade≥1 派生",
            rows[0]["gold_headings"] == ["第三章 > 养老", "第四章 > 税"],
            f"got={rows[0]['gold_headings']}",
        )

        # 非法 grade 必须报错
        bad = {"q1": {"reviewed": ["c1"], "candidates": {"c1": {"grade": 5}}}}
        (td / "bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        try:
            merge_grades(td, td / "bad.json", td / "y.jsonl")
            check_true("非法 grade 应报错", False, "未抛出 SystemExit")
        except SystemExit:
            check_true("非法 grade 正确报错", True)

        # 候选池与标签完全不匹配时必须报错（否则静默产出"整题无关"）
        mismatch = {"q1": {"reviewed": ["不存在的块"], "candidates": {"不存在的块": {"grade": 2}}}}
        (td / "mismatch.json").write_text(
            json.dumps(mismatch, ensure_ascii=False), encoding="utf-8"
        )
        try:
            merge_grades(td, td / "mismatch.json", td / "z.jsonl")
            check_true("候选池与标签不匹配应报错", False, "未抛出 SystemExit")
        except SystemExit:
            check_true("候选池与标签不匹配正确报错", True)

        # ---- 覆盖率过低必须报错 ----
        # 注意：merge_grades 固定读 pool_dir / "pool.jsonl"，因此**必须覆盖同名文件**。
        # （早期版本写成 pool_big.jsonl，函数根本读不到，于是测的是旧的 3 候选池，
        #   覆盖率 1/3 = 33.3% 恰好高于 30% 门槛 → 断言永远失败。）
        pool_big = [
            {
                "id": "q1",
                "query": "查询一",
                "category": "policy",
                "answerable": True,
                "note": "",
                "candidates": [
                    {"chunk_id": f"b{i}", "heading_path": [f"H{i}"]} for i in range(10)
                ],
            }
        ]
        (td / "pool.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pool_big) + "\n",
            encoding="utf-8",
        )
        low = {"q1": {"reviewed": ["b0"], "candidates": {"b0": {"grade": 2}}}}
        (td / "low.json").write_text(json.dumps(low, ensure_ascii=False), encoding="utf-8")
        try:
            merge_grades(td, td / "low.json", td / "x.jsonl")
            check_true(
                "低覆盖率应报错",
                False,
                "未抛出 SystemExit（覆盖率 1/10 = 10%，应低于 30% 门槛）",
            )
        except SystemExit:
            check_true("低覆盖率正确报错", True)

        # ---- --seed-from-gold：未人工标注的候选按 system_gold 派生为 2 / 0 ----
        pool_seed = [
            {
                "id": "q1",
                "query": "查询一",
                "category": "policy",
                "answerable": True,
                "note": "",
                "candidates": [
                    {"chunk_id": "g1", "heading_path": ["H1"]},
                    {"chunk_id": "g2", "heading_path": ["H2"]},
                ],
            }
        ]
        (td / "pool.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pool_seed) + "\n",
            encoding="utf-8",
        )
        seed_labels = {
            "q1": {
                "reviewed": [],
                "candidates": {
                    "g1": {"grade": 0, "system_gold": True},
                    "g2": {"grade": 0, "system_gold": False},
                },
            }
        }
        (td / "seed.json").write_text(
            json.dumps(seed_labels, ensure_ascii=False), encoding="utf-8"
        )
        out2 = td / "graded2.jsonl"
        stats2 = merge_grades(td, td / "seed.json", out2, seed_from_gold=True)
        check("派生：gold 候选记为 grade=2", stats2["grade_hist"][2], 1)
        check("派生：非 gold 记为 grade=0", stats2["grade_hist"][0], 1)
        check("派生：统计到 derived 数量", stats2["derived"], 2)
        check_true("派生后覆盖率 100%", abs(stats2["coverage"] - 1.0) < 1e-9)
        rows2 = [
            json.loads(x) for x in out2.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        check("派生：gold_headings 只含 gold 项", rows2[0]["gold_headings"], ["H1"])


def test_query_case_grades() -> None:
    print("\n[6] QueryCase 分级标注解析")

    class Case:
        pass

    from wealth_rag.evaluation import QueryCase

    c = QueryCase(id="x", query="q", grades={"a": 2, "b": 1})
    check("grade_of 命中", c.grade_of("a"), 2)
    check("grade_of 未标注 → 0", c.grade_of("zzz"), 0)
    check_true("有 gold 时 answerable", QueryCase(id="x", query="q", gold_headings=["h"]).answerable)
    check_true(
        "负例类别不可答",
        not QueryCase(id="x", query="q", category="负例-荐股", gold_headings=["h"]).answerable,
    )


def _run(label: str, fn) -> None:
    """跑一个子测试，保证输出顺序可读。

    被测函数会直接往 stdout 写字（`merge_grades` 内部的 rich 表格绕过了 print 的
    缓冲），不 flush 的话表格会挤到下一个子测试的标题之前，看起来像是顺序错乱。
    """
    sys.stdout.flush()
    fn()
    sys.stdout.flush()


def main() -> int:
    # 清空失败列表：本模块的 _FAILURES 是模块级的，若在同一进程内多次调用 main()
    # （例如被 tests/rag_tools 的测试套件导入执行），上一次的失败会被重复计入。
    _FAILURES.clear()

    print("=" * 64)
    print("校准逻辑自检（不加载模型）")
    print("=" * 64)
    _run("[1]", test_percentile)
    _run("[2]", test_choose_threshold)
    _run("[3]", test_classify)
    _run("[4]", test_graded_ndcg)
    _run("[5]", test_merge_grades)
    _run("[6]", test_query_case_grades)

    print("\n" + "=" * 64)
    if _FAILURES:
        print(f"❌ {len(_FAILURES)} 项失败：")
        for f in _FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
