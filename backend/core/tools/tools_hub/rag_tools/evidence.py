"""第 3 层｜**适配层**：内部 ``SearchResult`` → 给 LLM 的证据块 + 三档质量判定。

纯标准库，不依赖 torch / qdrant / wealth_rag——因此可以脱离模型被单测直接验证。

设计要点（对应 `问题文档/问题与解决方案.md` 的 P2-13 / P2-14 / P0-11）：

1. **内部模型 ≠ 对外契约。** ``SearchResult`` 是内部 dataclass，携带 UUID、本地绝对路径、
   与 citation 重复的面包屑文本、以及内部分数。本模块只保留**最小充分信息**。
2. **编号由后端给。** 载荷里的 ``index`` 是引用机制的锚点（``[1]`` / ``[2]``），
   不能让模型自己数。
3. **分数不给模型。** ``score`` / ``rerank_score`` 只留在 ``EvidenceItem`` 上供调用方使用，
   不写进给 LLM 的载荷——否则模型会把"相关度 0.89"复述给用户。

**本模块的函数不会进工具注册表**：`tools_route` 只注册 `tools_schema.py`
声明过的名字，这里没有一个是工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "QUALITY_HIGH",
    "QUALITY_LOW",
    "QUALITY_MEDIUM",
    "QUALITY_HINT",
    "EvidenceItem",
    "answer_directive",
    "classify_quality",
]

# ======================================================================
# 证据质量三档（对应 config.retrieval 的三个阈值参数）
# ======================================================================

QUALITY_HIGH = "high"  # 重排器确认有高度相关证据
QUALITY_MEDIUM = "medium"  # 重排器不确定；排序已退回关键词路
QUALITY_LOW = "low"  # 重排器判定全都不相关 —— 库中可能没有相关证据

QUALITY_HINT = {
    QUALITY_HIGH: "证据充分：正常作答，但每处结论都要标注来源编号。",
    QUALITY_MEDIUM: "证据有限：可以作答，但必须说明结论基于有限资料，"
    "不要给出确定性的数额或条款。",
    QUALITY_LOW: "证据不足：不要凭记忆作答。应告知用户知识库中未找到相关依据，"
    "并建议其咨询专业机构或补充信息。",
}


# ======================================================================
# 轻量结果类型
# ======================================================================


@dataclass
class EvidenceItem:
    """从 ``SearchResult`` 抽取的、对外需要的字段。

    刻意**不含**：``chunk_id`` / ``parent_id``（UUID 对 LLM 零信息量）、
    ``source_path``（本地绝对路径，泄露目录结构）、``text``（含面包屑前缀，与 citation 重复）。
    """

    raw_text: str  # 证据正文（已含叶子标题）
    doc_title: str = ""
    page: int | None = None
    heading_path: list[str] = field(default_factory=list)
    chunk_type: str = "text"
    parent_text: str | None = None
    #: 排序分（RRF 或融合分）——只给调用方，不给 LLM
    score: float = 0.0
    #: 重排原始分——只给调用方，不给 LLM
    rerank_score: float | None = None

    @property
    def citation(self) -> str:
        """可读来源引用：``文档标题 | 第 N 页 | 章 > 节``。"""
        loc = f"第 {self.page} 页" if self.page else ""
        path = " > ".join(self.heading_path or [])
        return " | ".join(p for p in (self.doc_title, loc, path) if p)

    def with_parent(self) -> "EvidenceItem":
        """浅拷贝（保留 ``parent_text``）。

        预算试探时会临时给某条挂上父块，用拷贝避免污染原对象。
        """
        return EvidenceItem(
            raw_text=self.raw_text,
            doc_title=self.doc_title,
            page=self.page,
            heading_path=list(self.heading_path),
            chunk_type=self.chunk_type,
            parent_text=self.parent_text,
            score=self.score,
            rerank_score=self.rerank_score,
        )

    @classmethod
    def from_search_result(cls, res) -> "EvidenceItem":
        """从 ``wealth_rag.models.SearchResult`` 构造。

        用 ``getattr`` 逐个取值，使本模块不 import wealth_rag——
        测试时可以传任何具备同名属性的替身对象（duck typing）。
        """
        return cls(
            raw_text=getattr(res, "raw_text", "") or "",
            doc_title=getattr(res, "doc_title", "") or "",
            page=getattr(res, "page", None),
            heading_path=list(getattr(res, "heading_path", None) or []),
            chunk_type=getattr(res, "chunk_type", "text") or "text",
            parent_text=getattr(res, "parent_text", None),
            score=float(getattr(res, "score", 0.0) or 0.0),
            rerank_score=(
                None
                if getattr(res, "rerank_score", None) is None
                else float(res.rerank_score)
            ),
        )


# ======================================================================
# 质量分级（三档判定，对应 Retriever._fuse 的语义）
# ======================================================================


def classify_quality(
    items: list[EvidenceItem],
    no_answer_threshold: float,
    rerank_floor: float,
) -> tuple[str, float | None]:
    """按最高重排分判定证据质量，返回 ``(quality, top_rerank_score)``。

    与 ``wealth_rag.retrieval.hybrid.Retriever._fuse`` 的三档保持一致：

    ===========================  ==========  ===================================
    条件                          质量         上层动作
    ===========================  ==========  ===================================
    ``top < no_answer_threshold``  ``low``      拒答（库中可能无相关证据）
    ``top >= rerank_floor``        ``high``     正常作答 + 引用
    其余                            ``medium``   作答但声明证据有限
    ===========================  ==========  ===================================

    **为什么在集成层重新判定，而不是直接用 ``SearchResult.low_confidence``**：
    后者是**二值**的，把「重排器明确否定」与「重排器不确定」合并成了同一档。
    而这两者的上层动作完全不同（拒答 vs 降级作答）。原始 ``top_rerank_score``
    也一并返回，让上层可以自行定策略而不用被写死的布尔值绑住。

    未开启重排（``rerank_score`` 全为 ``None``）时返回 ``medium``——
    没有重排信号时既不能确认也不能否定，保守地要求「声明证据有限」。
    """
    if not items:
        return QUALITY_LOW, None

    scores = [it.rerank_score for it in items if it.rerank_score is not None]
    if not scores:
        # 未重排：无从判断，降级为 medium（要求声明不确定性），不误判为 low（那会拒答）
        return QUALITY_MEDIUM, None

    top = max(scores)
    if top < no_answer_threshold:
        return QUALITY_LOW, top
    if top >= rerank_floor:
        return QUALITY_HIGH, top
    return QUALITY_MEDIUM, top


def answer_directive(quality: str) -> str:
    """把质量档位翻译成给上层（Agent/LLM）的动作指令。"""
    return {
        QUALITY_HIGH: "answer",
        QUALITY_MEDIUM: "answer_with_caveat",
        QUALITY_LOW: "abstain",
    }.get(quality, "answer_with_caveat")
