"""核心数据模型：Chunk（摄入单元）与 SearchResult（检索结果）。

Chunk 采用 mom/text 两级结构（对齐 RAGFlow TokenChunker 设计）：

- text 子块：参与嵌入与检索的最小单元；
- mom 父块：所属「节」的全文，供命中后做上下文扩展。

【P2-2】父块的存法：早期版本把 ``parent_text`` **复制**进每一个子块的 payload，
若某节拆成 10 个子块，节全文就被存 10 份。现在改为**引用式**——
子块 payload 只保留 ``parent_id``，父块作为一个独立的（无向量的）点存一次，
检索命中子块后按 ``parent_id`` 回查。实测 Qdrant 本地模式接受无向量点，
且这类点不会被向量检索召回。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    text: str  # 参与嵌入的文本（含面包屑前缀）
    raw_text: str  # 返回给 LLM 的证据文本（前置叶子标题）
    doc_id: str
    doc_title: str
    source_path: str
    page: int | None
    heading_path: list[str] = field(default_factory=list)
    chunk_type: str = "text"  # text | table | table_continued | parent
    parent_id: str | None = None  # mom 块引用
    parent_text: str | None = None  # mom 块全文（摄入时用于生成父块点，**不入子块 payload**）

    def to_payload(self) -> dict:
        """子块 payload。

        【P2-2】刻意**不含** ``parent_text``：父块全文由独立点承载，
        子块只保留 ``parent_id`` 引用，避免同一节全文被复制 N 份。
        """
        return {
            "text": self.text,
            "raw_text": self.raw_text,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "source_path": self.source_path,
            "page": self.page,
            "heading_path": self.heading_path,
            "chunk_type": self.chunk_type,
            "parent_id": self.parent_id,
        }

    def parent_payload(self) -> dict | None:
        """父块点的 payload（无向量，不参与检索）。"""
        if not self.parent_id or not self.parent_text:
            return None
        return {
            "text": self.parent_text,
            "raw_text": self.parent_text,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "source_path": self.source_path,
            "page": self.page,
            "heading_path": self.heading_path,
            "chunk_type": "parent",
            "parent_id": None,
        }


@dataclass
class SearchResult:
    chunk_id: str
    score: float  # 最终排序得分（RRF，或 RRF+重排融合后的得分）
    rerank_score: float | None  # 重排原始分（未重排时为 None）
    text: str
    raw_text: str
    doc_id: str
    doc_title: str
    source_path: str
    page: int | None
    heading_path: list[str] = field(default_factory=list)
    chunk_type: str = "text"
    parent_id: str | None = None
    parent_text: str | None = None
    #: 【P1-4】重排器明确判定「全都不相关」时为 True —— 库中可能根本没有相关证据。
    #: 上层编排应据此走「没有找到可靠证据」分支，而不是拿低质片段强行生成答案。
    low_confidence: bool = False

    @property
    def citation(self) -> str:
        """可读的来源引用，供后续编排阶段直接拼接。"""
        loc = f"第 {self.page} 页" if self.page else ""
        path = " > ".join(self.heading_path)
        parts = [self.doc_title, loc, path]
        return " | ".join(p for p in parts if p)
