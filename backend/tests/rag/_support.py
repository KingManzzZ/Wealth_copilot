"""测试共用：路径引导、替身对象、环境探测。

**核心设计：双重替身（duck typing）**

集成代码（`_rag_impl.py` / `rag_evidence.py`）刻意**不 import** `wealth_rag`
与 `torch`，只用 ``getattr`` 访问检索结果与 settings 的属性。
因此测试可以注入完全合成的替身对象，从而：

- 不需要 torch / transformers / qdrant
- 不需要已摄入的知识库
- 不消耗 GPU，毫秒级完成

这既是"测试友好"，也是**架构上的好处**——集成层与检索引擎的耦合面被压到最小
（只有 7 个属性名），任何一方重构都不会牵动另一方。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

# ======================================================================
# 路径引导：让测试能 import backend 下的两套代码
# ======================================================================

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parents[1]  # backend/
RAG_SRC = BACKEND_DIR / "RAG_" / "src"

for p in (str(BACKEND_DIR), str(RAG_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


def env_flag(name: str) -> bool:
    """读取布尔型环境变量（1/true/yes/on）。"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def rag_config_path() -> Path:
    """RAG 模块的 config.yaml 路径（与 ``wealth_rag.paths`` 同一口径）。

    优先级：``WEALTH_RAG_CONFIG`` → 模块根 ``RAG_/config.yaml``。
    相对路径先按 CWD、再按 RAG 模块根，保证测试在仓库根与模块根之间切换时不失配。
    """
    root = BACKEND_DIR / "RAG_" / "config.yaml"
    env = os.environ.get("WEALTH_RAG_CONFIG")
    if not env:
        return root
    p = Path(env).expanduser()
    if p.is_absolute():
        return p
    cwd_candidate = (Path.cwd() / p).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    module_candidate = p if p.is_absolute() else (BACKEND_DIR / "RAG_" / p).resolve()
    return module_candidate if module_candidate.exists() else cwd_candidate


def has_rag_deps() -> bool:
    """真实检索所需依赖是否可用（torch + transformers + qdrant_client）。"""
    try:
        import qdrant_client  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def has_knowledge_base() -> bool:
    """知识库是否已摄入（Qdrant 本地目录存在且集合非空）。

    只做目录级检查，不打开数据库——打开会加文件锁，可能与正在跑的服务冲突。
    """
    qdrant_dir = rag_config_path().parent / "db" / "qdrant"
    if not qdrant_dir.exists():
        return False
    # Qdrant 本地模式会建 storage.sqlite；有它就认为库存在
    return any(qdrant_dir.rglob("*.sqlite"))


# ======================================================================
# 替身：检索结果
# ======================================================================


class FakeSearchResult:
    """模仿 ``wealth_rag.models.SearchResult`` 的对外属性子集。

    只实现集成层真正读取的 7 个属性。多出来的属性（``chunk_id`` /
    ``parent_id`` / ``source_path`` / ``text``）**故意不做成可序列化字段**，
    用于验证适配层确实把它们丢掉了。
    """

    def __init__(
        self,
        raw_text: str = "证据正文",
        doc_title: str = "测试文档",
        page: int | None = 1,
        heading_path: list[str] | None = None,
        chunk_type: str = "text",
        parent_text: str | None = None,
        score: float = 0.5,
        rerank_score: float | None = 0.8,
        chunk_id: str = "11111111-1111-1111-1111-111111111111",
        parent_id: str | None = None,
        source_path: str = r"E:\Python_Projects\Agent_study\backend\RAG_\data\test.pdf",
        text: str = "面包屑前缀 > 证据正文",
    ):
        self.raw_text = raw_text
        self.doc_title = doc_title
        self.page = page
        self.heading_path = heading_path if heading_path is not None else ["第一章", "第一节"]
        self.chunk_type = chunk_type
        self.parent_text = parent_text
        self.score = score
        self.rerank_score = rerank_score
        self.chunk_id = chunk_id
        self.parent_id = parent_id
        self.source_path = source_path
        self.text = text


def make_results(n: int = 3, rerank_score: float = 0.8, **kwargs) -> list[FakeSearchResult]:
    """造 n 条替身结果。"""
    return [
        FakeSearchResult(
            raw_text=f"第 {i} 条证据的正文内容。" + "补充说明。" * 5,
            heading_path=["第一章", f"第 {i} 节"],
            rerank_score=rerank_score,
            **kwargs,
        )
        for i in range(1, n + 1)
    ]


# ======================================================================
# 替身：假 LLM（流式 OpenAI 客户端）
# ======================================================================
#
# 第 6 层（test_agent_events）与第 7 层（test_api_e2e）都需要一个"按脚本吐流式
# 响应、不联网"的假客户端。这两份实现曾各自复制在各自的测试文件里，逐字重复
# 约 130 行，改一处必然漏另一处——因此收敛到这里，只此一份。
#
# 两层的用法差异只有一处：第 6 层会断言"每轮流都被 close"，所以这里始终记录
# ``streams``，第 7 层不用即可。


class FakeStream:
    """可迭代、可 close 的假流对象（对应 SDK 的 Stream）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


def Delta(content=None, reasoning_content=None, tool_calls=None):
    """一个流式 delta（对应 SDK 的 ChoiceDelta）。"""
    return SimpleNamespace(
        content=content, reasoning_content=reasoning_content, tool_calls=tool_calls
    )


def chunk(delta):
    """把一个 delta 包成 chunk（对应 SDK 的 ChatCompletionChunk）。"""
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def tool_call_delta(index: int, call_id: str, name: str, args: str):
    """一个工具调用 delta。``args`` 是**原始字符串**（故意不解析成 dict）。"""
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


class FakeOpenAI:
    """按脚本依次返回流式响应。

    ``script`` 是 ``list[list[chunk]]``：每次 ``chat.completions.create`` 消费一条，
    脚本耗尽后返回空流（模拟"模型不再输出"）。

    - ``requests`` 记录每次调用的 kwargs，供断言"参数/Messages 是否真的传下去了"
    - ``streams`` 记录每次返回的流对象，供断言"流是否被 close"

    用法::

        fake = FakeOpenAI(answer_script("你好"))
        agent_mod.client = fake          # 替换模块级客户端，无需真实 API key
    """

    def __init__(self, script):
        self._script = list(script)
        self.requests: list[dict] = []
        self.streams: list[FakeStream] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                chunks = outer._script.pop(0) if outer._script else []
                stream = FakeStream(chunks)
                outer.streams.append(stream)
                return stream

        self.chat = SimpleNamespace(completions=_Completions())


def answer_script(text: str = "我是 WealthPilot。"):
    """一轮脚本：只有回答文本、无工具调用 → Agent 结束循环。"""
    return [[chunk(Delta(reasoning_content="先想一想…")), chunk(Delta(content=text))]]


def tool_round_script(tool: str = "this_tool_does_not_exist", args: str = "{}", final: str = "完成"):
    """两轮脚本：第一轮调工具，第二轮给出回答。"""
    return [
        [
            chunk(Delta(content="我来查一下。")),
            chunk(Delta(tool_calls=[tool_call_delta(0, "call_1", tool, args)])),
        ],
        *answer_script(final),
    ]


# ======================================================================
# 替身：settings / store / retriever
# ======================================================================


class FakeRetrievalConfig:
    def __init__(self, **kw):
        self.dense_top = kw.get("dense_top", 30)
        self.sparse_top = kw.get("sparse_top", 30)
        self.fused_top = kw.get("fused_top", 20)
        self.final_top = kw.get("final_top", 8)
        self.rrf_k = kw.get("rrf_k", 60)
        self.rerank_weight = kw.get("rerank_weight", 0.6)
        self.rerank_floor = kw.get("rerank_floor", 0.25)
        self.no_answer_threshold = kw.get("no_answer_threshold", 0.05)
        self.dedupe_by_parent = kw.get("dedupe_by_parent", True)


class FakeRerankConfig:
    def __init__(self, **kw):
        self.enabled = kw.get("enabled", True)
        self.model = kw.get("model", "Qwen/Qwen3-Reranker-0.6B")
        self.max_length = kw.get("max_length", 4096)
        self.batch_size = kw.get("batch_size", 8)
        self.instruct = kw.get("instruct", "配置里的原始重排指令")


class FakeSettings:
    def __init__(self, retrieval=None, rerank=None):
        self.retrieval = retrieval or FakeRetrievalConfig()
        self.rerank = rerank or FakeRerankConfig()


class FakeStore:
    """模仿 ``QdrantStore`` 的 ``stats()``。"""

    def __init__(self, chunks: int = 100, docs: int = 2):
        self._chunks = chunks
        self._docs = docs

    def stats(self) -> dict:
        return {"chunks": self._chunks, "docs": self._docs, "doc_ids": []}


class FakeRetriever:
    """模仿 ``wealth_rag.retrieval.hybrid.Retriever``。

    - ``search()`` 返回预置结果或抛预置异常
    - ``settings`` 可被集成层就地改写（用于验证 options 是否生效）
    - ``embedder`` / ``reranker`` 提供 ``load()``，用于验证预热路径
    - 记录 ``calls``，便于断言参数是否真的传下去了
    """

    def __init__(
        self,
        results=None,
        settings=None,
        raise_on_search: Exception | None = None,
        store=None,
    ):
        self._results = results if results is not None else make_results(3)
        self.settings = settings or FakeSettings()
        self._raise = raise_on_search
        self.store = store if store is not None else FakeStore()
        self.calls: list[dict] = []
        self.loaded = False

        outer = self

        class _Embedder:
            def load(self_inner):
                outer.loaded = True

        class _Reranker:
            def __init__(self_inner):
                self_inner.cfg = outer.settings.rerank

            def load(self_inner):
                outer.loaded = True

        self.embedder = _Embedder()
        self.reranker = _Reranker()

    def search(self, query: str, top_k: int | None = None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, **kwargs})
        if self._raise is not None:
            raise self._raise
        return list(self._results)


__all__ = [
    "BACKEND_DIR",
    "Delta",
    "FakeOpenAI",
    "FakeRerankConfig",
    "FakeRetrievalConfig",
    "FakeRetriever",
    "FakeSearchResult",
    "FakeSettings",
    "FakeStore",
    "FakeStream",
    "RAG_SRC",
    "TESTS_DIR",
    "answer_script",
    "chunk",
    "env_flag",
    "has_knowledge_base",
    "has_rag_deps",
    "make_results",
    "rag_config_path",
    "tool_call_delta",
    "tool_round_script",
]
