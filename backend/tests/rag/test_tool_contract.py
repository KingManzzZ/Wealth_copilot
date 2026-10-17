"""工具契约测试：schema 与注册表必须一致。

**为什么这个测试重要**

`tools_schema.py`（给模型看的声明）与 `tools_hub/`（实际实现）是**两份手工维护的清单**。
任何一边改了另一边没改，都不会报错：

- schema 里有、实现里没有 → 模型会调用一个不存在的工具，返回"未知工具"错误
- 实现里有、schema 里没有 → 工具永远不会被调用（静默失效）

这正是 `问题文档/问题与解决方案.md` 里反复出现的「静默失效」模式。
本模块把它变成**可自动检测的验收项**。

注意：本测试会 import `core.tools.tools_route`，从而触发工具自动发现并打开数据库
（连接是懒建的，但 package import 会有副作用）。这是有意为之——
契约测试就应该在真实注册表上跑。
"""

from __future__ import annotations

import json
import sys

import _support
from run_tests import Skip, eq, in_, not_in, true


def _fake_engine():
    """构造一个使用替身 Retriever 的引擎，并装进进程级单例。

    **为什么必须这样做**：`search_knowledge` 会走 `rag_tools.impl.get_engine()`，
    若不替换，契约测试会真的加载两个 0.6B 模型（约 2.3GB、十几秒）——
    那会让"契约测试"变成"等模型"，与分层设计的初衷相悖。
    """
    from core.tools.tools_hub.rag_tools import impl as _rag_impl

    engine = _rag_impl.RagEngine(
        config_path=_support.rag_config_path(),
        retriever_factory=lambda _cfg: _support.FakeRetriever(),
    )
    _rag_impl.reset_engine(engine)
    return engine

# ======================================================================
# 加载注册表与 schema
# ======================================================================


def _load():
    try:
        from core.tools import tools_route, tools_schema
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"无法 import 工具层（缺少依赖？）：{type(exc).__name__}: {exc}")
    return tools_route, tools_schema


def _schema_names(tools_schema) -> set[str]:
    return {
        item["function"]["name"]
        for item in tools_schema.TOOLS_SCHEMA
        if item.get("type") == "function"
    }


# ======================================================================
# 注册与配对
# ======================================================================

def check_search_knowledge_registered():
    tools_route, _ = _load()
    in_(
        "search_knowledge",
        tools_route.TOOL_REGISTRY,
        "search_knowledge 应被 tools_hub 自动发现",
    )
    true(
        callable(tools_route.TOOL_REGISTRY["search_knowledge"]),
        "注册项应可调用",
    )


def check_search_knowledge_has_schema():
    _, tools_schema = _load()
    in_("search_knowledge", _schema_names(tools_schema), "schema 里应有 search_knowledge")


def check_every_schema_has_implementation():
    """schema 声明的工具必须都有实现，否则模型会调用到不存在的工具。"""
    tools_route, tools_schema = _load()
    missing = sorted(_schema_names(tools_schema) - set(tools_route.TOOL_REGISTRY))
    eq(missing, [], f"这些 schema 声明没有对应实现：{missing}")


def check_registry_equals_schema():
    """**注册表必须恒等于 schema**（不是"子集"，也不是"多几个已知项"）。

    这是把 `tools_route` 改成"以 schema 为准建注册表"之后的核心不变量：

    - 集合相等 ⇒ 数量相等，用户看到的"对不上"从根上不可能再发生；
    - 既不允许少（缺实现 → 模型调用到不存在的工具），
      也不允许多（多出无 schema 的函数 → 注册表不是能力清单）。

    在此之前，registry 里会混进 ``classify_quality`` / ``build_evidence_payload``
    / ``prewarm`` 这类非工具函数，实测出现过 **28 项 vs schema 24 项**。
    当时的应对是维护一份"已知非工具名单"——那是**补丁**，不是修复：
    名单每加一个新函数就要更新一次，且它默认"多余项是正常的"。
    现在改为**断言相等**，多余项直接失败（见问题文档 P2-28）。
    """
    tools_route, tools_schema = _load()
    schema = _schema_names(tools_schema)
    registry = set(tools_route.TOOL_REGISTRY)

    eq(
        sorted(registry - schema),
        [],
        "注册表里有 schema 未声明的项——它不该被注册。\n"
        "        若这是新工具，请在 tools_schema.py 补契约；\n"
        "        若这是内部函数，它本来就不该进注册表（检查它是否被误当作实现）。",
    )
    eq(
        sorted(schema - registry),
        [],
        "schema 声明了但没有实现——模型会调用到不存在的工具。",
    )
    eq(
        len(tools_route.TOOL_REGISTRY),
        len(schema),
        f"注册表 {len(tools_route.TOOL_REGISTRY)} 项 ≠ schema {len(schema)} 项",
    )


def check_no_private_functions_registered():
    """下划线开头的函数不应出现在注册表里（P1-10 / P2-25 的回归防线）。

    这条曾经真实失败过——``rag_evidence.py`` 里的 ``_payload_chars`` /
    ``_render_payload`` / ``_with_parent`` 被自动发现注册成了工具，
    因为 `tools_route` 原先只在**模块级**跳过下划线，不管模块内的私有函数。

    现在的双重保证：
    1. 组装逻辑搬到了 ``_rag_impl.py``（模块名带下划线 → 整个模块被跳过）；
    2. `tools_route` 尊重模块的 ``__all__``（符号级过滤）。
    """
    tools_route, _ = _load()
    private = sorted(n for n in tools_route.TOOL_REGISTRY if n.startswith("_"))
    eq(private, [], f"私有函数不应被注册为工具：{private}")


def check_all_exports_are_declared_where_present():
    """显式声明 ``__all__`` 的工具模块，注册项必须都在 ``__all__`` 内。

    这是对 `tools_route` 符号级过滤的直接验证：只要模块声明了 ``__all__``，
    注册表就不应出现它之外的名字。
    """
    import importlib

    tools_route, _ = _load()
    hub = "core.tools.tools_hub"
    for mod_name in (
        "rag_tools",
        "asset_tools",
        "basic_info_tools",
        "cashflow_tools",
        "debt_tools",
        "goal_tools",
        "transaction_tools",
    ):
        try:
            mod = importlib.import_module(f"{hub}.{mod_name}")
        except Exception:  # noqa: BLE001
            continue
        declared = getattr(mod, "__all__", None)
        if declared is None:
            continue
        leaked = sorted(
            n
            for n in tools_route.TOOL_REGISTRY
            if getattr(tools_route.TOOL_REGISTRY[n], "__module__", "") == mod.__name__
            and n not in set(declared)
        )
        eq(leaked, [], f"{mod_name} 声明了 __all__，但注册表里有额外项：{leaked}")


# ======================================================================
# schema 本身的质量
# ======================================================================

def check_search_knowledge_schema_shape():
    _, tools_schema = _load()
    spec = next(
        i for i in tools_schema.TOOLS_SCHEMA if i["function"]["name"] == "search_knowledge"
    )
    fn = spec["function"]
    eq(spec["type"], "function")
    params = fn["parameters"]
    eq(params["type"], "object")
    in_("query", params["properties"], "应有 query 参数")
    eq(params["required"], ["query"], "query 是唯一必需参数")
    # top_k / include_parent 可选
    in_("top_k", params["properties"])
    in_("include_parent", params["properties"])
    eq(params["properties"]["include_parent"]["type"], "boolean")


def check_description_discourages_misuse():
    """description 必须写清"什么时候不要用"以及 query 的构造要求。

    这不是文案洁癖——`search_knowledge` 最主要的两类失败是：
    ① 拿用户私域数据去检索（「我的收入」在知识库里没有对应内容）；
    ② 传对话原句（含指代，检索质量断崖）。
    两者都只能靠 description 约束，所以必须有回归测试锁住。
    """
    _, tools_schema = _load()
    desc = next(
        i for i in tools_schema.TOOLS_SCHEMA if i["function"]["name"] == "search_knowledge"
    )["function"]["description"]

    for keyword in ("不适用", "资产", "计算"):
        in_(keyword, desc, f"description 应说明不适用范围（缺 {keyword}）")
    in_("独立检索", desc, "description 应要求 query 可独立检索")
    in_("指代", desc, "description 应要求补全指代")
    in_("数值", desc, "description 应要求把用户数值先取出来再写进 query")


def check_all_descriptions_nonempty():
    """所有工具的 description 都不能为空（空描述会让模型无法判断是否该调用）。"""
    _, tools_schema = _load()
    empty = sorted(
        i["function"]["name"]
        for i in tools_schema.TOOLS_SCHEMA
        if not (i["function"].get("description") or "").strip()
    )
    eq(empty, [], f"这些工具缺少 description：{empty}")


# ======================================================================
# 路由行为
# ======================================================================

def check_route_unknown_tool_returns_error():
    tools_route, _ = _load()
    raw = tools_route.route("this_tool_does_not_exist", "{}")
    payload = json.loads(raw)
    in_("error", payload, "未知工具应返回 error")
    in_("未知工具", payload["error"])


def check_route_bad_arguments_returns_error():
    """参数解析失败要返回 error 而不是抛异常（否则 Agent 循环会中断）。"""
    tools_route, _ = _load()
    payload = json.loads(tools_route.route("search_knowledge", "{not valid json"))
    in_("error", payload)


def check_route_empty_query_returns_error():
    """空 query 应被工具自己拦下（不触发引擎调用）。"""
    _fake_engine()
    tools_route, _ = _load()
    payload = json.loads(tools_route.route("search_knowledge", '{"query": "   "}'))
    in_("error", payload)
    in_("query", payload["error"])
    eq(payload["directive"], "abstain")


def check_route_returns_json_string():
    """route 的返回值必须是 JSON 字符串——agent.py 会 json.loads 它。"""
    tools_route, _ = _load()
    raw = tools_route.route("this_tool_does_not_exist", "{}")
    true(isinstance(raw, str), "route 应返回字符串")
    json.loads(raw)  # 必须可解析


def check_top_k_clamped():
    """top_k 越界应由工具夹紧，而不是把越界值传给检索器。

    不夹紧的后果：模型传 top_k=100 会让重排跑 100 条，延迟成倍上升。
    """
    from core.tools.tools_hub.rag_tools import search_knowledge

    engine = _fake_engine()
    payload = search_knowledge("测试", top_k=999)
    true(isinstance(payload, dict), "越界 top_k 不应导致异常")
    not_in("error", payload, "替身引擎不应报错")
    true(payload["result_count"] <= 8, f"top_k 应被夹紧到 8 以内（实际 {payload['result_count']}）")

    payload2 = search_knowledge("测试", top_k="不是数字")
    true(isinstance(payload2, dict), "非法 top_k 不应导致异常")
    not_in("error", payload2, "非法 top_k 应降级为默认值而非报错")

    # 下界：0 或负数应被夹到 1
    payload3 = search_knowledge("测试", top_k=0)
    not_in("error", payload3)
    true(payload3["result_count"] >= 1, "top_k=0 应被夹到至少 1")


def check_include_parent_string_coercion():
    """模型可能把布尔值传成字符串，工具应容忍。"""
    from core.tools.tools_hub.rag_tools import search_knowledge

    _fake_engine()
    for raw in ("true", "True", "1", "是"):
        payload = search_knowledge("测试", include_parent=raw)
        true(isinstance(payload, dict), f"include_parent={raw!r} 不应导致异常")
        not_in("error", payload, f"include_parent={raw!r} 应被正确转换")


# ======================================================================
# 导入自包含（P2-26：不再依赖"装过 wealth-rag"）
# ======================================================================

def check_wealth_rag_importable_without_install():
    """``import wealth_rag`` 必须只靠仓库内路径就能成立。

    RAG 原本靠 ``pyproject.toml`` + ``pip install -e`` 提供 ``wealth_rag``；
    收编后 ``pyproject.toml`` 已删除，因此集成层必须自己把 ``RAG_/src`` 补进
    ``sys.path``（见 `_rag_impl._ensure_wealth_rag_importable`）。

    这条测试防的是 P2-26：**在"没装过这个包"的环境里静默失效**。
    它不真正构造 Retriever（那会加载 2.3GB 模型），只验证**导入链路可解析**。
    """
    import importlib.util

    from core.tools.tools_hub.rag_tools import impl as _rag_impl

    src = _rag_impl.DEFAULT_RAG_ROOT / "src"
    if not src.is_dir():
        raise Skip(f"未找到 RAG 源码目录（跳过）：{src}")

    added = _rag_impl.ensure_importable()
    eq(str(src), added, "应把 RAG_/src 加入 sys.path")

    # 幂等：重复调用不应重复插入
    n_before = len([p for p in sys.path if p == str(src)])
    _rag_impl.ensure_importable()
    n_after = len([p for p in sys.path if p == str(src)])
    eq(n_after, 1, f"ensure_importable 应幂等（插入前 {n_before}，后 {n_after}）")

    # 只解析导入，不执行模块顶层重逻辑
    spec = importlib.util.find_spec("wealth_rag")
    true(spec is not None, "加入 src 后应能解析到 wealth_rag 包")
    for sub in ("wealth_rag.config", "wealth_rag.retrieval.hybrid"):
        true(
            importlib.util.find_spec(sub) is not None,
            f"应能解析 {sub}（集成层 _build() 依赖它）",
        )


def check_no_pyproject_left_in_rag():
    """RAG 目录不应再有 ``pyproject.toml``（依赖已并入根 requirements.txt）。

    防的是 P2-25：两个依赖清单并存会让新环境静默缺依赖。
    允许"空文件占位"（便于人工删除），但**不允许**它再声明 project / build-system。
    """
    from core.tools.tools_hub.rag_tools import impl as _rag_impl

    p = _rag_impl.DEFAULT_RAG_ROOT / "pyproject.toml"
    if not p.exists():
        return  # 已删除，符合预期
    text = p.read_text(encoding="utf-8")
    for forbidden in ("[project]", "[project.scripts]", "[build-system]", "[tool.setuptools]"):
        not_in(
            forbidden,
            text,
            f"RAG 的 pyproject.toml 不应再声明 {forbidden}（依赖应归口根 requirements.txt）",
        )


def check_root_requirements_covers_rag_deps():
    """根 requirements.txt 必须覆盖 RAG 的运行依赖（P2-25 的回归防线）。"""
    from core.tools.tools_hub.rag_tools import impl as _rag_impl

    req = _rag_impl.BACKEND_DIR.parent / "requirements.txt"
    if not req.exists():
        raise Skip(f"未找到根 requirements.txt：{req}")
    text = req.read_text(encoding="utf-8").lower()

    # 检索链路的最小必需集（缺任何一个，search_knowledge 都会失败）
    for dep in ("torch", "transformers", "qdrant-client", "jieba", "tiktoken", "numpy"):
        in_(dep, text, f"根 requirements.txt 缺 RAG 依赖：{dep}")


def check_tool_returns_json_serializable():
    """工具返回值必须可直接 json.dumps（tools_route 会做这一步）。"""
    from core.tools.tools_hub.rag_tools import search_knowledge

    _fake_engine()
    payload = search_knowledge("资产配置", top_k=2, include_parent=True)
    json.dumps(payload, ensure_ascii=False)  # 不应抛异常
