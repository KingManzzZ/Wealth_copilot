"""配置加载：config.yaml → pydantic 模型（路径均相对配置文件的目录解析）。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_NAME = "config.yaml"

#: PDF 的 CID→Unicode 映射表损坏时产生的乱码汉字（本项目语料实测）。
#: 这些字符是**系统性**的——同一字形永远映射到同一个错字，且常插在词与词之间，
#: 会同时破坏分词（BM25 失配）、正则（标题识别失败）与嵌入（语义被噪声稀释）。
#: 处理方式：整体替换为空格以恢复词边界（见 parser.normalize_garbled）。
DEFAULT_GARBLED_CHARS = "摇郾也冶页盂藔"


class PathsConfig(BaseModel):
    data_dir: Path = Path("./data")
    db_dir: Path = Path("./db")
    models_dir: Path = Path("./models")
    cache_dir: Path = Path("./cache")


class EmbeddingConfig(BaseModel):
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    dim: int = 1024
    max_length: int = 8192
    batch_size: int = 8
    instruct: str = (
        "Given a financial question, retrieve relevant passages that answer the question"
    )


class RerankConfig(BaseModel):
    enabled: bool = True
    model: str = "Qwen/Qwen3-Reranker-0.6B"
    max_length: int = 4096
    batch_size: int = 8
    instruct: str = "Given a financial query, retrieve passages that answer the query"


class SparseConfig(BaseModel):
    method: str = "qdrant_sparse"  # qdrant_sparse | jieba_bm25
    k1: float = 1.5
    b: float = 0.75


class RetrievalConfig(BaseModel):
    dense_top: int = 30
    sparse_top: int = 30
    fused_top: int = 20
    final_top: int = 8
    rrf_k: int = 60
    rerank_weight: float = 0.6  # 重排分在最终融合中的权重（0=纯 RRF，1=纯重排）
    rerank_floor: float = 0.25  # 重排最高分低于该阈值 → 判定重排不可靠，退回 RRF 排序
    no_answer_threshold: float = 0.05  # 重排最高分低于该阈值 → 判定库中无相关证据
    dedupe_by_parent: bool = True  # 按 parent_id / heading_path 去重，保证召回多样性


class ChunkingConfig(BaseModel):
    max_tokens: int = 512
    delimiters: str = "\n!?。；！？"
    #: 允许的最大**语义层级**（不再约束 Markdown 的 # 数量）。
    #: 7 = 覆盖到「第X条」；默认放宽，避免 PyMuPDF 通道的 H5/H6 标题被整层丢弃。
    heading_max_level: int = 7
    overlap_tokens: int = 0  # 块间重叠 token 数（0 = 不重叠）
    breadcrumb: bool = True  # 面包屑注入（标题路径前缀，提升嵌入与关键词匹配上下文）
    #: 面包屑首层是否带文档标题。⚠️ 权衡：会给同文档的**所有**块加同一前缀
    #: （对稠密向量是常量成分，轻度压缩区分度），跨文档消歧时才有收益。
    include_doc_title: bool = True
    running_header_min_count: int = 3  # 标题重复 ≥N 次且近似 doc_title → 判为页眉并丢弃
    drop_contents: bool = True  # 删除目录区块（目录标题 + 目录表格）
    title_rules: bool = True  # 启用中文标题层级识别与伪标题过滤
    min_chunk_chars: int = 30  # 小于该长度的块并入同节相邻块
    merge_documents: bool = True  # 识别合订本中的文书边界并重置层级栈


class ParsingConfig(BaseModel):
    page_break_placeholder: str = "<!--PAGEBREAK-->"
    pdf_engine: str = "auto"  # auto（Docling 优先，失败回退 PyMuPDF）| docling | pymupdf
    garbled_chars: str = DEFAULT_GARBLED_CHARS  # 乱码字符表，替换为空格
    #: 【P0-8】章级大纲重建：PDF 无书签、章标题在文本层缺失时，
    #: 用「目录页 + 页眉」重建 (物理页 → 章) 映射并注入合成章标题。
    rebuild_outline: bool = True


class Settings(BaseModel):
    """全局配置。"""

    paths: PathsConfig = Field(default_factory=PathsConfig)
    device: str = "auto"
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    sparse: SparseConfig = Field(default_factory=SparseConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)

    #: 配置文件所在目录（运行期填充，不参与序列化）
    root: Path = Field(default=Path("."), exclude=True)

    def resolve(self) -> "Settings":
        """把相对路径解析为绝对路径，并创建必要目录。"""
        for name in ("data_dir", "db_dir", "models_dir", "cache_dir"):
            p: Path = getattr(self.paths, name)
            if not p.is_absolute():
                p = (self.root / p).resolve()
            setattr(self.paths, name, p)
        self.paths.db_dir.mkdir(parents=True, exist_ok=True)
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_device(self) -> str:
        """auto → 有 CUDA 用 cuda，否则 cpu。"""
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def resolve_model_path(self, model: str) -> str:
        """优先使用 models_dir 下的本地模型目录，否则返回原始模型名（HF/ModelScope 拉取）。"""
        local = self.paths.models_dir / model.split("/")[-1]
        return str(local) if local.exists() else model


def load_settings(config_path: str | Path | None = None) -> Settings:
    """加载配置文件。默认读取项目根目录 config.yaml，可用环境变量 WEALTH_RAG_CONFIG 指定。"""
    if config_path is None:
        config_path = os.environ.get("WEALTH_RAG_CONFIG", DEFAULT_CONFIG_NAME)
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = Settings(**raw)
    settings.root = path.parent
    return settings.resolve()