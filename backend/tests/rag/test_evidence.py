"""证据适配层测试（``tools_hub/rag_evidence.py`` + ``_rag_impl.py`` 的组装函数）。

覆盖三类风险，都对应真实踩过的坑（见 `问题文档/问题与解决方案.md`）：

1. **信息泄露**（P2-14）：`SearchResult` 里的 UUID、本地绝对路径、内部相似度分
   不能进 LLM 上下文。
2. **质量分级**（P0-11 / P0-12）：二值 `low_confidence` 把「明确否定」与
   「不确定」合并了，必须拆成三档，且未重排时不能误判为「该拒答」。
3. **体积失控**（P2-13）：`parent_text` 无上限，必须**严格**守住预算
   （早期是"尽力而为"，实测出现请求 900 字符却返回 4314 字符的情况）。

数据模型与分级在 `rag_evidence.py`；载荷组装与预算裁剪在 `_rag_impl.py`
（放后者是因为它文件名带下划线，不会被 `tools_route` 的自动发现扫到 —— 见 P2-25）。
"""

from __future__ import annotations

import json

import _support
from run_tests import close, eq, in_, not_in, true

from core.tools.tools_hub.rag_tools.evidence import (
    QUALITY_HIGH,
    QUALITY_LOW,
    QUALITY_MEDIUM,
    EvidenceItem,
    answer_directive,
    classify_quality,
)

# 组装函数在 impl（引擎层），不在 evidence（适配层）
from core.tools.tools_hub.rag_tools.impl import (
    build_demo_payload,
    build_evidence_payload,
)

# 通过模块名访问替身类（不用 from _support import ...，保证只有一份 _support 实例）
FakeSearchResult = _support.FakeSearchResult
make_results = _support.make_results


# ======================================================================
# EvidenceItem.from_search_result
# ======================================================================

def check_from_search_result_maps_fields():
    res = FakeSearchResult(
        raw_text="正文",
        doc_title="金融知识普及读本",
        page=42,
        heading_path=["第三章", "个人养老金"],
        rerank_score=0.89,
        score=0.7,
    )
    item = EvidenceItem.from_search_result(res)
    eq(item.raw_text, "正文")
    eq(item.doc_title, "金融知识普及读本")
    eq(item.page, 42)
    eq(item.heading_path, ["第三章", "个人养老金"])
    close(item.rerank_score, 0.89)
    close(item.score, 0.7)


def check_citation_format():
    item = EvidenceItem(raw_text="x", doc_title="读本", page=3, heading_path=["第一章", "第一节"])
    eq(item.citation, "读本 | 第 3 页 | 第一章 > 第一节")
    # 缺页码
    eq(EvidenceItem(raw_text="x", doc_title="读本", heading_path=["第一章"]).citation, "读本 | 第一章")
    # 只有标题
    eq(EvidenceItem(raw_text="x", doc_title="读本").citation, "读本")
    # 全空
    eq(EvidenceItem(raw_text="x").citation, "")


def check_from_search_result_tolerates_missing_attrs():
    """缺属性不应崩——集成层用 getattr 取值，正是为了让替身可以更简陋。"""

    class Bare:
        raw_text = "只有正文"

    item = EvidenceItem.from_search_result(Bare())
    eq(item.raw_text, "只有正文")
    eq(item.doc_title, "")
    eq(item.page, None)
    eq(item.rerank_score, None)


# ======================================================================
# 质量分级（三档）
# ======================================================================

def check_classify_low_when_all_below_no_answer():
    items = [EvidenceItem(raw_text="a", rerank_score=0.001)]
    quality, top = classify_quality(items, 0.05, 0.25)
    eq(quality, QUALITY_LOW, "顶分 < no_answer_threshold → low")
    close(top, 0.001)


def check_classify_high_when_above_floor():
    items = [EvidenceItem(raw_text="a", rerank_score=0.3), EvidenceItem(raw_text="b", rerank_score=0.9)]
    quality, top = classify_quality(items, 0.05, 0.25)
    eq(quality, QUALITY_HIGH, "顶分 ≥ rerank_floor → high")
    close(top, 0.9, msg="顶分应取最大值")


def check_classify_medium_in_between():
    items = [EvidenceItem(raw_text="a", rerank_score=0.1)]
    quality, top = classify_quality(items, 0.05, 0.25)
    eq(quality, QUALITY_MEDIUM, "中间档 → medium")
    close(top, 0.1)


def check_classify_boundaries():
    """边界值必须明确定义（否则线上判定与校准结果会差一条样本）。"""
    # 恰好等于 no_answer_threshold → 不判 low（约定：< 才是 low）
    eq(classify_quality([EvidenceItem(raw_text="a", rerank_score=0.05)], 0.05, 0.25)[0], QUALITY_MEDIUM)
    # 恰好等于 rerank_floor → 判 high（约定：>= 就是 high）
    eq(classify_quality([EvidenceItem(raw_text="a", rerank_score=0.25)], 0.05, 0.25)[0], QUALITY_HIGH)


def check_classify_no_rerank_is_medium_not_low():
    """未重排时必须判 medium，不能判 low。

    判 low 会让上层**拒答**——而"没有重排信号"不等于"库里没有证据"。
    这个区分正是 P0-11 要解决的问题（二值 `low_confidence` 做不到）。
    """
    items = [EvidenceItem(raw_text="a", rerank_score=None), EvidenceItem(raw_text="b")]
    quality, top = classify_quality(items, 0.05, 0.25)
    eq(quality, QUALITY_MEDIUM, "无重排信号 → medium（要求声明不确定性），不是 low")
    eq(top, None, "无重排信号时顶分为 None")


def check_classify_empty_is_low():
    quality, top = classify_quality([], 0.05, 0.25)
    eq(quality, QUALITY_LOW, "空结果 → low（库里没东西）")
    eq(top, None)


def check_directive_mapping():
    eq(answer_directive(QUALITY_HIGH), "answer")
    eq(answer_directive(QUALITY_MEDIUM), "answer_with_caveat")
    eq(answer_directive(QUALITY_LOW), "abstain")
    eq(answer_directive("unknown-value"), "answer_with_caveat", "未知档位应保守降级")


# ======================================================================
# 载荷组装与信息瘦身
# ======================================================================

def _payload(items, **kw):
    kw.setdefault("query", "测试查询")
    kw.setdefault("quality", QUALITY_HIGH)
    return build_evidence_payload(items, **kw)


def check_payload_shape():
    p = _payload([EvidenceItem.from_search_result(r) for r in make_results(2)], top_score=0.88)
    eq(p["evidence_quality"], QUALITY_HIGH)
    eq(p["query_used"], "测试查询")
    eq(p["result_count"], 2)
    eq(len(p["results"]), 2)
    true("hint" in p, "应带 hint 提示")
    # 编号由后端给，从 1 开始连续
    eq([r["index"] for r in p["results"]], [1, 2])
    for r in p["results"]:
        true("citation" in r and "text" in r, "每条应含 citation 与 text")


def check_payload_no_internal_fields():
    """核心安全断言：内部字段与本地路径不能出现在给 LLM 的载荷里。"""
    items = [
        EvidenceItem.from_search_result(
            FakeSearchResult(
                chunk_id="deadbeef-1111-2222-3333-444444444444",
                parent_id="cafebabe-1111-2222-3333-444444444444",
                source_path=r"E:\Python_Projects\Agent_study\backend\RAG_\data\secret.pdf",
                text="面包屑 > 正文",
            )
        )
    ]
    raw = json.dumps(_payload(items), ensure_ascii=False)
    for leaked in ("deadbeef", "cafebabe", "chunk_id", "parent_id", "source_path", "E:\\\\"):
        not_in(leaked, raw, f"{leaked} 不应出现在给 LLM 的载荷中")


def check_payload_includes_citation_not_breadcrumb_dup():
    """citation 里已有标题路径，正文不应再重复整条面包屑。"""
    item = EvidenceItem.from_search_result(
        FakeSearchResult(raw_text="正文内容", heading_path=["第三章", "个人养老金"])
    )
    raw = json.dumps(_payload([item]), ensure_ascii=False)
    in_("第三章 > 个人养老金", raw, "citation 应包含标题路径")
    # 正文只有 raw_text，不含 text 字段的"面包屑前缀 > "形态
    not_in("面包屑前缀", raw, "不应把嵌入用的 text 塞进载荷")


def check_parent_excluded_by_default():
    item = EvidenceItem.from_search_result(
        FakeSearchResult(raw_text="子块正文", parent_text="整节全文" * 100)
    )
    p = _payload([item])
    eq(p["results"][0].get("parent_text"), None, "默认不应附父块")


def check_parent_included_when_asked():
    item = EvidenceItem.from_search_result(
        FakeSearchResult(raw_text="子块正文", parent_text="整节全文")
    )
    p = _payload([item], include_parent=True)
    eq(p["results"][0].get("parent_text"), "整节全文", "开启后应附父块")


# ======================================================================
# 预算裁剪
# ======================================================================

def check_budget_drops_whole_items():
    """超预算时整条丢弃，并说明丢了几条。"""
    items = [EvidenceItem(raw_text="X" * 400, rerank_score=0.9 - i * 0.01) for i in range(10)]
    p = build_evidence_payload(
        items, query="q", quality=QUALITY_HIGH, top_score=0.9, budget_chars=1500
    )
    true(p["result_count"] < 10, f"应丢弃部分条目（实际保留 {p['result_count']}）")
    true(p["result_count"] >= 1, "至少保留 1 条")
    in_("notice", p, "裁剪后应给出 notice")
    in_("保留", p["notice"], "notice 应说明保留了前几条")
    # 每条都是完整的（没有被截断）
    for r in p["results"]:
        eq(len(r["text"]), 400, "条目正文应完整，不应被截断")


def check_budget_respected():
    items = [EvidenceItem(raw_text="Y" * 300, rerank_score=0.9) for _ in range(8)]
    budget = 2000
    p = build_evidence_payload(
        items, query="q", quality=QUALITY_HIGH, top_score=0.9, budget_chars=budget
    )
    size = len(json.dumps(p, ensure_ascii=False))
    true(size <= budget, f"载荷体积 {size} 应 ≤ 预算 {budget}")


def check_budget_single_item_overflow():
    """单条就超预算时：**截断该条**并标记，而不是整条超出去。

    早期实现是"宁可超预算也不能不给证据"，结果请求 900 字符却返回了 4314 字符——
    调用方无法通过 ``budget_chars`` 控制上下文体积，而这个参数存在的唯一理由就是控制它。
    现在改为：截断 + ``text_truncated`` 标记 + notice。
    """
    items = [EvidenceItem(raw_text="Z" * 5000, rerank_score=0.9)]
    budget = 800
    p = build_evidence_payload(
        items, query="q", quality=QUALITY_HIGH, top_score=0.9, budget_chars=budget
    )
    eq(p["result_count"], 1, "仍应返回 1 条")
    size = len(json.dumps(p, ensure_ascii=False))
    true(size <= budget, f"截断后载荷 {size} 应 ≤ 预算 {budget}")
    eq(p.get("text_truncated"), True, "应带 text_truncated 标记，让上层知道正文不完整")
    in_("notice", p)
    in_("截断", p["notice"])
    true(len(p["results"][0]["text"]) < 5000, "正文应被截短")


def check_budget_single_item_respects_min_floor():
    """预算设得极小时按 MIN_BUDGET_CHARS 下限执行，不产出无意义的碎片。"""
    from core.tools.tools_hub.rag_tools.impl import MIN_BUDGET_CHARS

    items = [EvidenceItem(raw_text="W" * 5000, rerank_score=0.9)]
    p = build_evidence_payload(
        items, query="q", quality=QUALITY_HIGH, top_score=0.9, budget_chars=1
    )
    size = len(json.dumps(p, ensure_ascii=False))
    true(
        size <= MIN_BUDGET_CHARS,
        f"应按下限 {MIN_BUDGET_CHARS} 执行（实际 {size}）",
    )
    true(len(p["results"][0]["text"]) > 0, "仍应有非空正文")


def check_parent_respects_budget():
    """父块只在剩余预算内附加；放不下就跳过，但不影响子块正文的存在。"""
    items = [
        EvidenceItem(raw_text="短正文一", parent_text="父块全文" * 500, rerank_score=0.9),
        EvidenceItem(raw_text="短正文二", parent_text="父块全文" * 500, rerank_score=0.8),
    ]
    budget = 900
    p = build_evidence_payload(
        items,
        query="q",
        quality=QUALITY_HIGH,
        top_score=0.9,
        include_parent=True,
        budget_chars=budget,
    )
    size = len(json.dumps(p, ensure_ascii=False))
    true(size <= budget, f"含父块时也应守住预算（实际 {size}）")
    true(p["result_count"] >= 1, "至少保留 1 条子块正文")
    # 两条子块正文都在（父块可缺失，子块正文不可缺）
    texts = [r["text"] for r in p["results"]]
    true(all("短正文" in t for t in texts), "子块正文不应因父块挤占而丢失")
    # 父块太大放不下 → 不应有一条带着巨大 parent_text 混进来
    total_parent = sum(len(r.get("parent_text") or "") for r in p["results"])
    true(
        total_parent < 10000,
        f"放不下的父块不应被附加（父块总长 {total_parent}）",
    )


def check_parent_attached_when_budget_allows():
    """预算充足时父块应被附上（别为了守预算把功能关掉）。"""
    items = [
        EvidenceItem(raw_text="子块正文", parent_text="整节全文内容", rerank_score=0.9),
    ]
    p = build_evidence_payload(
        items,
        query="q",
        quality=QUALITY_HIGH,
        top_score=0.9,
        include_parent=True,
        budget_chars=6000,
    )
    eq(p["results"][0].get("parent_text"), "整节全文内容", "预算充足时应附父块")


def check_payload_no_per_item_scores():
    """逐条分数不进载荷（只留顶分），避免模型把分数当可信度复述给用户。"""
    items = [EvidenceItem.from_search_result(r) for r in make_results(3, 0.9)]
    p = _payload(items, top_score=0.9)
    for r in p["results"]:
        not_in("rerank_score", r, "逐条 rerank_score 不应出现在证据条目里")
        not_in("score", r, "内部排序分不应出现在证据条目里")
    json.dumps(p, ensure_ascii=False)


def check_empty_items_returns_low():
    p = build_evidence_payload([], query="q", quality=QUALITY_LOW)
    eq(p["result_count"], 0)
    eq(p["results"], [])
    eq(p["evidence_quality"], QUALITY_LOW)


# ======================================================================
# 自检载荷
# ======================================================================

def check_demo_payload_is_marked():
    """自检载荷必须明确标注"非真实证据"，防止模型据此回答用户。"""
    p = build_demo_payload()
    eq(p["result_count"], 1)
    in_("notice", p)
    in_("自检", p["notice"], "notice 必须说明这是自检数据")
    in_("禁止", p["notice"], "notice 必须禁止据此回答")
    eq(p["evidence_quality"], QUALITY_HIGH, "自检载荷用高分以验证 high 通路")


def check_payload_is_json_serializable():
    items = [EvidenceItem.from_search_result(r) for r in make_results(5)]
    p = _payload(items, include_parent=True, top_score=0.9)
    json.dumps(p, ensure_ascii=False)  # 不应抛异常
