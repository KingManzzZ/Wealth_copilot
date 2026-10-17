"""第 2 层｜**引擎**：进程内单例 Retriever + 预热 + 证据组装与预算裁剪。

``` 
rag_tools/
├── __init__.py     ← 对外只 re-export search_knowledge
├── tools.py        ← 第 1 层：工具入口（参数防御 → 转交本模块）
├── impl.py         ← 第 2 层：本文件（引擎 + 组装）
└── evidence.py     ← 第 3 层：适配层（数据模型 + 三档分级）
```

**资源生命周期（对应问题文档 P2-15）**

- ``Retriever`` 是**模块级单例**：编码器与重排器各 1.15GB，每次工具调用重建会导致
  反复 ``from_pretrained``，首次十几秒、之后每次数百毫秒到数秒，还有显存累积风险。
- ``prewarm()`` 把冷启动从「用户第一次提问」挪到「服务启动」。
- 所有检索调用走同一把 ``threading.Lock`` 串行化——8GB 显存下并发不会提升吞吐，
  只会让显存峰值叠加、延迟互相拖累。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .evidence import (
    QUALITY_HINT,
    QUALITY_LOW,
    EvidenceItem,
    answer_directive,
    classify_quality,
)

# 本文件位置：backend/core/tools/tools_hub/rag_tools/impl.py
# → parents[0]=rag_tools, [1]=tools_hub, [2]=tools, [3]=core, [4]=backend
_THIS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = _THIS_DIR.parents[4]

#: RAG 模块根目录（含 config.yaml 与 src/）
DEFAULT_RAG_ROOT = BACKEND_DIR / "RAG_"

#: 可用环境变量覆盖配置位置（便于测试与多环境部署）
ENV_RAG_CONFIG = "WEALTH_RAG_CONFIG"


def _ensure_wealth_rag_importable() -> None:
    """把 ``backend/RAG_/src`` 加进 ``sys.path``，让 ``import wealth_rag`` 可用。

    RAG 原本是独立项目，靠 ``pyproject.toml`` + ``pip install -e`` 提供 ``wealth_rag``
    与 ``rag`` 命令。收编进 backend 后 `pyproject.toml` 已删除、依赖并入根
    ``requirements.txt``，因此**不能再假设这个包被安装过**。

    这里显式补路径，让后端**自包含**。（RAG 目录下的 `docs/audit/*.py` 与
    `eval/*.py` 本来就用同样手法自己插 ``src``，所以这是项目内既有约定。）

    **代价与替代**：若希望完全不碰 ``sys.path``，可在根 ``requirements.txt`` 里写
    ``-e ./backend/RAG_`` 并保留 ``pyproject.toml``——但那等于把该目录重新变成
    可安装项目，与"取消独立项目状态"的目标相反。详见问题文档 P2-26。

    幂等：重复调用只插一次。
    """
    src = DEFAULT_RAG_ROOT / "src"
    if not src.is_dir():
        return
    p = str(src)
    if p not in sys.path:
        sys.path.insert(0, p)


def ensure_importable() -> str | None:
    """对外暴露的路径引导（供测试或诊断脚本调用）。

    返回被加入 ``sys.path`` 的目录；RAG 目录不存在时返回 ``None``。
    """
    _ensure_wealth_rag_importable()
    src = DEFAULT_RAG_ROOT / "src"
    return str(src) if src.is_dir() else None


# ======================================================================
# 证据载荷组装与预算裁剪
# ======================================================================

#: 载荷预算的硬下界。低于此值连一条像样的证据都放不下，
#: 与其返回被截断到无意义的正文，不如按这个下限执行（由调用方保证语义）。
MIN_BUDGET_CHARS = 200


def _render_payload(
    items: list[EvidenceItem],
    query: str,
    quality: str,
    top_score: float | None,
    include_parent: bool,
    notes: list[str],
    truncated: bool = False,
) -> dict:
    """把证据条目渲染成最终给 LLM 的载荷。"""
    results = []
    for i, it in enumerate(items, start=1):
        entry = {
            "index": i,
            "citation": it.citation,
            "text": it.raw_text,
            "chunk_type": it.chunk_type,
        }
        if include_parent and it.parent_text:
            entry["parent_text"] = it.parent_text
        results.append(entry)

    # ⚠️ 逐条的 rerank 分**不进**载荷：模型会把它当"可信度"复述给用户
    # （"该证据相关度 0.89"），对用户毫无意义且污染输出风格。
    # 只保留顶分这一个聚合值——它是"证据够不够"的判断依据。
    payload = {
        "evidence_quality": quality,
        "top_rerank_score": None if top_score is None else round(top_score, 6),
        "query_used": query,
        "result_count": len(results),
        "results": results,
        "hint": QUALITY_HINT.get(quality, ""),
    }
    if truncated:
        payload["text_truncated"] = True
    if notes:
        payload["notice"] = "；".join(notes)
    return payload


def _payload_chars(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def build_evidence_payload(
    items,
    query: str,
    quality: str,
    top_score: float | None = None,
    include_parent: bool = False,
    budget_chars: int = 6000,
) -> dict:
    """组装给 LLM 的证据 JSON，并**严格**把体积控制在 ``budget_chars`` 内。

    **预算保证是硬约束，不是尽力而为。** 早期实现是"尽力而为"：
    单条证据超预算时直接返回整条，实测出现请求 900 字符预算却返回 4314 字符的情况。
    那样调用方就无法通过这个参数控制上下文体积——而控制体积正是它存在的理由。

    **三级降级**（优先级从高到低）：

    1. **减少条数**：预算内放尽量多条完整证据（整条保留，不切碎——
       截断会让证据语义不完整，例如只剩「每年缴纳上限为」而没有数字，比不给更危险）；
    2. **截断唯一一条**：若连一条都放不下，则截断该条正文并标 ``text_truncated``；
    3. **父块按需**：父块是增强信息，只占用剩余预算，放不下就跳过（不挤占子块正文）。

    **字符而非 token 做预算**：token 计数需要 tiktoken（有依赖、有加载成本）。
    字符预算是保守近似——中文约 1 token ≈ 1.5 字符，``budget_chars=6000``
    大致对应 4K token 以内。需要精确控制时调用方可传更小的预算。
    """
    items = list(items)
    if not items:
        return _render_payload([], query, QUALITY_LOW, None, include_parent, [])

    # 设下界：预算小到无法容纳任何有意义正文时，按最小可用集执行，
    # 而不是产出被截断到无意义的碎片。
    budget = max(int(budget_chars), MIN_BUDGET_CHARS)
    notes: list[str] = []
    truncated = False

    # ---- 第一级：尽量多条完整证据 ----
    kept = 0
    for n in range(1, len(items) + 1):
        trial = _render_payload(items[:n], query, quality, top_score, False, [])
        if _payload_chars(trial) <= budget:
            kept = n
        else:
            break

    selected = items[:kept]

    # ---- 第二级：一条都放不下 → 截断唯一一条 ----
    if not selected:
        first = items[0]
        # 二分收缩正文长度，直到装得下
        text = first.raw_text
        low, high = 0, len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            probe = first.with_parent()
            probe.raw_text = text[:mid]
            trial = _render_payload([probe], query, quality, top_score, False, [])
            if _payload_chars(trial) <= budget:
                best = text[:mid]
                low = mid + 1
            else:
                high = mid - 1
        probe = first.with_parent()
        probe.raw_text = best
        selected = [probe]
        truncated = True
        notes.append("单条证据超出预算，正文已截断")

    if 0 < kept < len(items):
        notes.append(f"为控制上下文体积，已从 {len(items)} 条中保留前 {kept} 条")

    # ---- 第三级：剩余预算内追加父块 ----
    if include_parent:
        remaining = budget - _payload_chars(
            _render_payload(selected, query, quality, top_score, False, notes, truncated)
        )
        if remaining <= 0:
            notes.append("预算已用尽，未附加父块上下文")
        else:
            # 逐条试探：某条的父块放不下就跳过它（不整体放弃）。
            # 父块是增强信息，可缺失；子块正文是必需信息，不可因父块而丢失。
            for idx in range(len(selected)):
                probe = list(selected)
                probe[idx] = probe[idx].with_parent()  # 拷贝，避免污染原对象
                if _payload_chars(
                    _render_payload(probe, query, quality, top_score, True, notes, truncated)
                ) <= budget:
                    selected = probe
                else:
                    # 该条的父块放不下 → 明确清掉，避免它带着 parent_text 混进结果
                    fallback = list(selected)
                    item = fallback[idx].with_parent()
                    item.parent_text = None
                    fallback[idx] = item
                    selected = fallback

    return _render_payload(
        selected, query, quality, top_score, include_parent, notes, truncated
    )


def build_demo_payload(
    top_score: float = 0.9,
    no_answer_threshold: float = 0.05,
    rerank_floor: float = 0.25,
) -> dict:
    """构造一份合成证据载荷，用于验证工具能否被调用（不依赖知识库）。

    用途：知识库尚未摄入时的自检通道——让开发者能在端到端链路上
    验证「工具注册 → 调用 → 返回 → 回填 messages」这条通路是通的，
    而把「检索质量」问题排除在外。
    """
    items = [
        EvidenceItem(
            raw_text="（自检数据，非真实证据）这是一条用于验证工具链路的合成证据。",
            doc_title="[自检] 知识库未就绪",
            page=1,
            heading_path=["自检"],
            rerank_score=top_score,
        )
    ]
    quality, top = classify_quality(items, no_answer_threshold, rerank_floor)
    payload = build_evidence_payload(items, "自检查询", quality, top)
    payload["notice"] = (
        "当前返回的是**自检数据**，不是真实知识库内容，禁止据此回答用户问题。"
    )
    return payload


# ======================================================================
# 可配置项
# ======================================================================


@dataclass
class RetrieveOptions:
    """单次检索的可调项。

    **为什么要这几个旋钮**：默认配置针对"离线批处理"设计（`fused_top=20`、
    重排 `max_length=4096`、`instruct` 面向纯检索）。接入交互式 Agent 后，
    延迟预算与调用语境都变了，需要能在**不修改 config.yaml** 的前提下调整：

    ====================  ==========================================================
    参数                  用途
    ====================  ==========================================================
    ``top_k``             最终返回条数（默认 5，落在"少而精"区间）
    ``fused_top``         进入重排的候选数——**重排延迟的直接乘数**（20 → 约 2 倍耗时）
    ``include_parent``    是否附整节全文（体积无上限，默认关）
    ``budget_chars``      证据载荷的字符预算（默认 6000 ≈ 4K token 以内）
    ``rerank_max_length`` 重排单条最大长度（默认用 config；缩短可显著降延迟）
    ``rerank_instruct``   重排指令。**Agent 语境下的查询形态与纯检索不同**
                          （口语化、含指代、往往带"我的情况"），可针对性改写
    ====================  ==========================================================
    """

    top_k: int = 5
    fused_top: int | None = None
    include_parent: bool = False
    budget_chars: int = 6000
    rerank_max_length: int | None = None
    rerank_instruct: str | None = None


#: 面向 Agent 对话语境的重排指令（比纯检索版更贴近"用户口吻的问题"）。
#: ⚠️ 置为 ``None`` 可改用 config.yaml 里的 ``rerank.instruct``——
#: 改了重排指令等于改了打分口径，**建议两者都跑一次评估集再定**（见 tests/rag/README.md）。
AGENT_RERANK_INSTRUCT: str | None = (
    "Given a user's question in a personal wealth planning conversation, "
    "judge whether the passage contains the information needed to answer it"
)


# ======================================================================
# 引擎
# ======================================================================


class RagEngine:
    """持有 Retriever 单例、负责检索与证据组装。

    **依赖注入**：``retriever_factory`` 可替换，便于测试时注入替身对象
    （不需要 torch / qdrant / 已摄入的知识库）。
    """

    def __init__(self, config_path: str | os.PathLike | None = None, retriever_factory=None):
        self._config_path = config_path
        self._retriever = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._load_ms: float | None = None
        self._factory = retriever_factory
        # 热路径计数器（便于测试与可观测性）
        self.stats = {"calls": 0, "errors": 0, "last_ms": None}

    # ------------------------------------------------------------------
    # 配置与加载
    # ------------------------------------------------------------------
    def config_path(self) -> Path:
        """解析配置文件路径：显式参数 > 环境变量 > 模块内默认。"""
        if self._config_path:
            return Path(self._config_path).expanduser().resolve()
        env = os.environ.get(ENV_RAG_CONFIG)
        if env:
            return Path(env).expanduser().resolve()
        return DEFAULT_RAG_ROOT / "config.yaml"

    def _build(self):
        """真正构造 Retriever（首次调用时执行一次）。"""
        cfg = self.config_path()
        if not cfg.exists():
            raise FileNotFoundError(
                f"RAG 配置文件不存在: {cfg}\n"
                f"可用环境变量 {ENV_RAG_CONFIG} 指定，或确认 RAG 模块位于 {DEFAULT_RAG_ROOT}"
            )
        if self._factory is not None:
            return self._factory(cfg)

        # RAG 已不再是可安装的独立项目（pyproject.toml 已删、依赖并入根 requirements.txt），
        # 因此这里先显式把 backend/RAG_/src 补进 sys.path，再 import。
        _ensure_wealth_rag_importable()

        # 延迟 import：让本模块在没有 torch / qdrant 的环境里也能被导入（测试友好）
        from wealth_rag.config import load_settings
        from wealth_rag.retrieval.hybrid import Retriever

        return Retriever(load_settings(str(cfg)))

    def _load_locked(self) -> None:
        """在**已持有** ``self._lock`` 的前提下构造 Retriever（只执行一次）。

        单独抽出来是为了让 ``search_knowledge`` 能在**同一把锁内**完成
        「加载 → 应用可选参数 → 检索」全流程，避免两次调用交错时
        A 设置的 ``fused_top`` 被 B 覆盖而 A 却按 B 的参数检索。
        """
        if self._retriever is not None:
            return
        t0 = time.perf_counter()
        try:
            self._retriever = self._build()
            self._load_error = None
        except Exception as exc:  # noqa: BLE001 —— 需要把原因原样带回给模型
            self._load_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._load_ms = round((time.perf_counter() - t0) * 1000, 1)

    def _ensure(self, options: RetrieveOptions | None = None):
        """取得 Retriever 并应用可选参数（幂等）。

        供 ``prewarm`` / ``selfcheck`` 等单线程路径使用；
        ``search_knowledge`` 走 ``with self._lock`` 内的 ``_load_locked`` + ``_apply_options``。
        """
        with self._lock:
            self._load_locked()
            self._apply_options(options)
            return self._retriever

    def _apply_options(self, options: RetrieveOptions | None) -> None:
        """把 RetrieveOptions 落到 settings/reranker 上（每次调用都重设，保证幂等）。"""
        if options is None:
            return
        s = self._retriever.settings
        if options.fused_top is not None:
            s.retrieval.fused_top = int(options.fused_top)
        if options.rerank_max_length is not None:
            s.rerank.max_length = int(options.rerank_max_length)
        # 重排指令：既改配置（供日志/复用），也改已实例化的 reranker（它自己缓存了 cfg 引用）
        if options.rerank_instruct:
            s.rerank.instruct = options.rerank_instruct
            reranker = getattr(self._retriever, "reranker", None)
            if reranker is not None:
                reranker.cfg.instruct = options.rerank_instruct

    def prewarm(self) -> dict:
        """预热：加载模型 + 跑一次占位检索，把冷启动挪到服务启动阶段。

        **必须吞异常**——RAG 是可选能力，其依赖缺失（未装 torch / 未下载模型 /
        知识库未摄入）不应导致整个 Agent 服务起不来。返回状态字典供日志与自检使用。
        """
        try:
            retriever = self._ensure(RetrieveOptions(top_k=1))
            # 触发编码器与重排器的实际加载（懒加载：不检索就不会 from_pretrained）
            embedder = getattr(retriever, "embedder", None)
            if embedder is not None:
                embedder.load()
            reranker = getattr(retriever, "reranker", None)
            if reranker is not None:
                reranker.load()
            return {
                "ok": True,
                "cold_start_ms": self._load_ms,
                "config": str(self.config_path()),
            }
        except Exception as exc:  # noqa: BLE001
            print(f"[rag] 预热失败，知识库检索工具将不可用：{type(exc).__name__}: {exc}")
            return {"ok": False, "error": str(exc), "config": str(self.config_path())}

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        include_parent: bool = False,
        budget_chars: int = 6000,
    ) -> dict:
        """检索知识库并返回可 JSON 序列化的证据载荷。

        任何异常都被翻译成 ``{"error": ...}``（``tools_route.route`` 依赖
        ``"error" in result`` 判定 ``status="error"``），让模型有机会降级回答，
        而不是让 Agent 循环整体失败。
        """
        options = RetrieveOptions(
            top_k=int(top_k),
            include_parent=bool(include_parent),
            budget_chars=int(budget_chars),
            rerank_instruct=AGENT_RERANK_INSTRUCT,
        )
        self.stats["calls"] += 1
        t0 = time.perf_counter()
        try:
            # 加载、应用参数、检索三步在同一把锁内完成：
            # ① 串行化 GPU 调用（8GB 显存下并发只会互相拖累）；
            # ② 防止两次调用交错时参数互相覆盖。
            with self._lock:
                self._load_locked()
                self._apply_options(options)
                raw = self._retriever.search(query, top_k=options.top_k)
                thresholds = (
                    self._retriever.settings.retrieval.no_answer_threshold,
                    self._retriever.settings.retrieval.rerank_floor,
                )
            items = [EvidenceItem.from_search_result(r) for r in raw]

            quality, top = classify_quality(items, *thresholds)
            payload = build_evidence_payload(
                items,
                query=query,
                quality=quality,
                top_score=top,
                include_parent=options.include_parent,
                budget_chars=options.budget_chars,
            )
            payload["directive"] = answer_directive(quality)
            if quality == QUALITY_LOW:
                payload.setdefault(
                    "notice",
                    "知识库中未检索到与该问题相关的可靠证据。请勿凭记忆作答。",
                )
            return payload
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            hint = ""
            if self._load_error:
                hint = "（RAG 依赖或知识库未就绪，详见 error 字段）"
            return {
                "error": f"{type(exc).__name__}: {exc}{hint}",
                "query_used": query,
                "evidence_quality": QUALITY_LOW,
                "directive": "abstain",
                "results": [],
                "notice": (
                    "知识库检索不可用。请如实告知用户当前无法查证资料，"
                    "不要凭记忆提供政策条款或具体数额。"
                ),
            }
        finally:
            self.stats["last_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # ------------------------------------------------------------------
    def selfcheck(self) -> dict:
        """自检：验证工具链路可用（不依赖知识库是否已摄入）。

        返回 ``{"ok": bool, "stage": str, ...}``，用于：
        - 服务启动日志
        - 测试中断言「工具可被调用」
        - 用户排查「为什么检索不到东西」
        """
        report: dict = {
            "config": str(self.config_path()),
            "config_exists": self.config_path().exists(),
        }
        if not report["config_exists"]:
            return {"ok": False, "stage": "config", **report}

        try:
            retriever = self._ensure(RetrieveOptions(top_k=1))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "load", "error": f"{type(exc).__name__}: {exc}", **report}

        try:
            store = getattr(retriever, "store", None)
            if store is not None:
                stats = store.stats()
                report["chunks"] = stats.get("chunks")
                report["docs"] = stats.get("docs")
                if not stats.get("chunks"):
                    return {
                        "ok": False,
                        "stage": "empty_index",
                        "hint": "知识库为空，请先执行：python -m wealth_rag.cli ingest ./data --rebuild",
                        **report,
                    }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "store", "error": f"{type(exc).__name__}: {exc}", **report}

        try:
            payload = self.search_knowledge("资产配置", top_k=1)
            report["probe_quality"] = payload.get("evidence_quality")
            report["probe_error"] = payload.get("error")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "search", "error": f"{type(exc).__name__}: {exc}", **report}

        return {"ok": True, "stage": "ready", **report}


# ======================================================================
# 进程级单例
# ======================================================================

_ENGINE: RagEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> RagEngine:
    """取得进程级唯一引擎实例。"""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = RagEngine()
    return _ENGINE


def reset_engine(engine: RagEngine | None = None) -> None:
    """替换/清空单例（**仅供测试使用**）。"""
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = engine


def prewarm() -> dict:
    """供 FastAPI lifespan 调用。"""
    return get_engine().prewarm()


def demo_payload() -> dict:
    """合成证据（知识库未就绪时的自检通道）。"""
    return build_demo_payload()


__all__ = [
    "AGENT_RERANK_INSTRUCT",
    "BACKEND_DIR",
    "DEFAULT_RAG_ROOT",
    "ENV_RAG_CONFIG",
    "MIN_BUDGET_CHARS",
    "RagEngine",
    "RetrieveOptions",
    "build_demo_payload",
    "build_evidence_payload",
    "demo_payload",
    "ensure_importable",
    "get_engine",
    "prewarm",
    "reset_engine",
]
