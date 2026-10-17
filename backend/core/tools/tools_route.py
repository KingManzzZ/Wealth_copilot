"""
工具注册表 + 路由。

## 注册表以 schema 为唯一依据（2026-10-15 起）

**旧做法**：扫描 ``tools_hub/`` 顶层所有 .py 文件，把其中"本模块内 def 的函数"
全部注册。问题是这样一来**注册表的项数由文件里写了几个函数决定**，
与 `tools_schema.py` 声明的工具数**天然会对不上**——实测出现过 28 项 vs 24 项：

```
[tools_route] 自动发现并注册的工具: [...
  '_payload_chars', '_render_payload', '_with_parent',        # 私有函数被注册
  'build_evidence_payload', 'classify_quality', ...           # 纯逻辑被注册
  'prewarm', 'demo_payload', ...]                             # 内部 API 被注册
```

**现做法**：先扫一遍 hub 建立「函数名 → 实现」的反向索引，然后
**只注册 `tools_schema.TOOLS_SCHEMA` 里声明过的名字**。于是：

- 注册表**恒等于** schema 的工具集，数量与名字都不会漂移；
- 模块里多写辅助函数**不影响**注册表；
- schema 声明了但没有实现 → `route()` 调用时报"未知工具"（明确失败，而非静默缺失）；
- 实现有了但 schema 没声明 → 不会被注册（正是我们要的：**schema 才是对外能力清单**）。

**代价**：新增工具必须同时改两处（schema + 实现）。由
`tests/rag_tools/test_tool_contract.py` 的契约测试守住这一点。

> 这个改动也顺带干掉了对 ``__all__`` / 下划线前缀的依赖——那两者都是需要
> 每个模块作者自觉遵守的**约定**，而"以 schema 为准"是**结构上的保证**
> （见问题文档 P2-28）。

## 调用期的三层兜底（2026-10-16 加固）

模型给的工具名是**不可信输入**，不能当成"算出来的答案"直接查表。``route()``
按三层处理，各层解决不同问题、互不可替代：

1. **归一化 + 分档模糊匹配**（``resolve_tool_name``）——救回"同一个名字的不同写法"
   与轻微拼写错误。这类近失**本不该失败**：``Get_Assets`` / ``get-assets`` /
   ``getAssets`` 都是同一个工具。
   ⚠️ 模糊匹配**只对只读工具开放**：把 ``update_asset`` 猜成 ``update_debt``
   会静默执行一个错误的写操作——调用成功、不报错、数据已改坏。
2. **按 schema 校验参数**（``validate_arguments``）——缺必填、幻觉参数、非对象
   都返回可操作的结构化错误，而不是把 Python 的 ``TypeError`` 原文丢给模型。
3. **结构化错误载荷**——未知工具时带上 ``available_tools`` 与 ``did_you_mean``。
   模型看到清单后重新选择，比在没有新信息的情况下重采样有效得多。

所有失败一律返回 ``{"error": ...}`` 而**不抛异常**：``agent.py`` 的循环依赖这一点
才能在工具出错后继续与模型对话。错误里带 ``retryable`` 区分"改一下再来"与
"重试无用，应当改口告知用户"。

> 背景与实证数据见 ``Investigation/tool幻觉兜底机制/``；
> 问题卡片见 ``问题文档/Agent-工具调用可靠性.md``。
"""

import difflib
import importlib
import inspect
import json
import os
import re
import sys

from . import tools_schema

#: 工具名 → 实现函数。**只包含 schema 里声明过的工具。**
TOOL_REGISTRY: dict = {}

#: 扫描过程中发现的、未在 schema 中声明的函数（诊断用，不参与路由）
_UNDECLARED: dict = {}

_HUB_DIR = os.path.join(os.path.dirname(__file__), "tools_hub")

#: hub 包的名字前缀，用于判定一个函数的归属模块是否在工具层内部
_HUB_PREFIX = f"{__package__}.tools_hub"


def _iter_module_names() -> tuple[list[str], str]:
    """递归列出 hub 下所有模块，**浅层优先**（保证注册结果稳定可复现）。

    工具既可以写成顶层模块（``asset_tools.py``），也可以写成子包
    （``rag_tools/{__init__,tools,impl,evidence}.py``）。因此扫描必须**递归**——
    否则子包里的实现会找不到，注册表就会缺工具。

    规则：
    - 跳过 ``_`` 开头的文件与目录（内部模块不参与扫描）；
    - 跳过 ``__pycache__``；
    - 目录视为包，取其 ``__init__.py``。
    """
    found: list[tuple[int, str]] = []

    def walk(abs_dir: str, rel_parts: list[str]) -> None:
        try:
            entries = sorted(os.listdir(abs_dir))
        except OSError:
            return
        for entry in entries:
            if entry.startswith("_") or entry == "__pycache__":
                continue
            abs_path = os.path.join(abs_dir, entry)
            if os.path.isdir(abs_path):
                if os.path.isfile(os.path.join(abs_path, "__init__.py")):
                    found.append((len(rel_parts), ".".join(rel_parts + [entry])))
                walk(abs_path, rel_parts + [entry])
            elif entry.endswith(".py"):
                found.append((len(rel_parts), ".".join(rel_parts + [entry[:-3]])))

    # 包所在目录：backend/core/tools/ → 其上溯 2 级得到 core 的父目录
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    walk(_HUB_DIR, [])
    # 先按目录深度、再按字典序：顶层模块优先，结果确定
    found.sort(key=lambda kv: (kv[0], kv[1]))
    return [f"{_HUB_PREFIX}.{name}" for _, name in found], pkg_root


def _belongs_to_hub(func) -> bool:
    """判断函数是否来自工具层内部（含子包内的定义与 re-export）。

    ``func.__module__`` 以 hub 前缀开头、且其子路径都是合法标识符，
    即认为是工具层内部的函数。这样既接受"本模块内 def"，
    也接受"从 hub 内其他模块 import 进来"（子包 ``__init__.py`` 的 re-export 属这种）。
    """
    module = getattr(func, "__module__", "") or ""
    if not module.startswith(_HUB_PREFIX + "."):
        return False
    tail = module[len(_HUB_PREFIX) + 1 :]
    # 排除 core.tools.tools_hub_extra 这类"前缀相同但不是子模块"的误判
    return all(part.isidentifier() for part in tail.split("."))


def _discover_implementations(hub_module_names: list[str], pkg_root: str) -> dict:
    """扫描 hub（含子包），建立「函数名 → 实现」索引。

    同一名字若在多处定义/导出，保留**先遇到**的那个
    （``_iter_module_names`` 已按"浅层优先 + 字典序"排序，结果确定可复现）。
    单个模块导入失败只跳过它，不拖垮整张工具表。
    """
    found: dict = {}
    pkg_dir = os.path.dirname(pkg_root)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)

    for module_name in hub_module_names:
        relative = module_name[len(_HUB_PREFIX) + 1 :]  # 去掉 core.tools.tools_hub.
        try:
            module = importlib.import_module(
                f".tools_hub.{relative}", package=__package__
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[tools_route] ⚠️ 加载 {module_name} 失败：{type(exc).__name__}: {exc}")
            continue
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if not _belongs_to_hub(func):
                continue
            found.setdefault(name, func)
    return found


def _build_registry() -> tuple[dict, dict]:
    """按 schema 收敛注册表。返回 ``(注册表, 未声明的实现)``。"""
    hub_module_names, pkg_root = _iter_module_names()
    implementations = _discover_implementations(hub_module_names, pkg_root)
    declared = [
        item["function"]["name"]
        for item in tools_schema.TOOLS_SCHEMA
        if item.get("type") == "function"
    ]

    registry: dict = {}
    missing: list[str] = []
    for name in declared:
        func = implementations.get(name)
        if func is None:
            missing.append(name)
            continue
        registry[name] = func

    undeclared = {k: v for k, v in implementations.items() if k not in registry}

    if missing:
        # 不抛异常：让服务仍能启动，把问题在启动日志里说清楚。
        # 这些工具被调用时会由 route() 返回"未知工具"，属明确失败。
        print(f"[tools_route] ⚠️ schema 声明但未找到实现：{missing}")
    print(
        f"[tools_route] 注册 {len(registry)} 个工具"
        f"（schema 声明 {len(declared)} 个）"
    )
    if undeclared:
        # 这些函数存在于 hub 中但不是工具（纯逻辑 / 内部 API）。它们不会被注册。
        # 列表可能较长（RAG 子包里有十几个），故只报数量；需要细查时读 _UNDECLARED。
        print(
            f"[tools_route] 另有 {len(undeclared)} 个非工具函数未被注册"
            "（正常，schema 才是能力清单）"
        )
    return registry, undeclared


TOOL_REGISTRY, _UNDECLARED = _build_registry()


# ======================================================================
# 第 1 层：工具名归一化与分档模糊匹配
# ======================================================================

#: 只读工具前缀。这些工具即使被"猜错"也不会改变任何状态，可以开放自动修复。
#: 写工具（record_/update_/delete_/create_/set_/complete_）**永不**自动修复。
_READ_ONLY_PREFIXES = ("get_", "calculate_", "search_", "list_")

#: 自动修复的相似度门槛（difflib ratio）。高于"建议"档，因为这里**会直接执行**。
_AUTO_REPAIR_CUTOFF = 0.85

#: 仅用于候选建议的门槛，只要求"看起来像"。
_SUGGEST_CUTOFF = 0.5

#: 候选建议最多返回几条
_SUGGEST_LIMIT = 3

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEP_RE = re.compile(r"[\s\-./:]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_NAMESPACE_RE = re.compile(r"^(?:functions?|tools?)\s*[.:]\s*", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """把模型给出的工具名归一到注册表使用的 snake_case 形式。

    处理四类常见书写漂移（这些都不是"编造"，只是写法不同）：

    - 命名空间前缀：``functions.get_assets`` → ``get_assets``
    - 驼峰：        ``getAssets``            → ``get_assets``
    - 大小写：      ``Get_Assets``           → ``get_assets``
    - 分隔符：      ``get-assets`` / ``get assets`` → ``get_assets``
    """
    s = str(name or "").strip()
    s = _NAMESPACE_RE.sub("", s)
    s = _CAMEL_RE.sub("_", s)
    s = s.lower()
    s = _SEP_RE.sub("_", s)
    return _MULTI_UNDERSCORE_RE.sub("_", s).strip("_")


#: 归一化索引：normalize_name(工具名) → 注册表里的真名
_NORMALIZED: dict = {}


def _rebuild_normalized_index() -> None:
    """重建归一化索引。注册表就绪后调用一次即可。"""
    _NORMALIZED.clear()
    for name in TOOL_REGISTRY:
        _NORMALIZED.setdefault(normalize_name(name), name)


def _is_read_only(tool_name: str) -> bool:
    """按前缀判断是否只读工具。"""
    return tool_name.startswith(_READ_ONLY_PREFIXES)


def resolve_tool_name(tool_name: str) -> tuple:
    """把模型给的名字解析成注册表里的真名。

    返回 ``(真名 或 None, 命中方式)``，命中方式取值：
    ``exact`` / ``normalized`` / ``fuzzy`` / ``none``。

    三级阶梯，代价递增、确定性递减：

    1. ``exact``      —— 原名就在注册表里；
    2. ``normalized`` —— 归一化后命中，**确定**，可以放心执行；
    3. ``fuzzy``      —— 模糊匹配，**只对只读工具开放**。

    第 3 级的限制是刻意的：``update_asset`` 拼错成 ``update_asst`` 时，
    模糊匹配能命中 ``update_asset``，但同样的机制也会把 ``update_asset``
    猜成 ``update_debt``。写工具错一次的代价（数据改坏、且没有任何报错）
    远大于"多问模型一轮"，所以写工具只给建议、不自动修复。
    """
    if tool_name in TOOL_REGISTRY:
        return tool_name, "exact"

    normalized = normalize_name(tool_name)
    hit = _NORMALIZED.get(normalized)
    if hit is not None:
        return hit, "normalized"

    matches = difflib.get_close_matches(
        normalized, _NORMALIZED.keys(), n=1, cutoff=_AUTO_REPAIR_CUTOFF
    )
    if matches:
        resolved = _NORMALIZED[matches[0]]
        if _is_read_only(resolved):
            return resolved, "fuzzy"
    return None, "none"


def _suggest_tool_names(tool_name: str) -> list:
    """给出候选工具名。**只做建议，绝不据此执行。**

    ⚠️ 这里的相似度**无法区分"合理近失"与"纯噪声"**：实测
    ``search_policy`` → ``search_knowledge`` 的 ratio 是 0.621，而
    ``nope_not_a_tool`` → ``complete_goal_tool`` 是 0.667——噪声反而更高，
    因为工具名共享领域词汇（``get_`` / ``_tool`` / ``_asset``）。
    所以提高阈值会先杀掉好的那条，唯一正确的用法是**交给模型判断**。
    """
    matches = difflib.get_close_matches(
        normalize_name(tool_name), _NORMALIZED.keys(), n=_SUGGEST_LIMIT, cutoff=_SUGGEST_CUTOFF
    )
    return [_NORMALIZED[m] for m in matches]


_rebuild_normalized_index()


# ======================================================================
# 第 2 层：按 schema 校验参数
# ======================================================================

#: 工具名 → 该工具的 parameters 声明
_TOOL_SPECS: dict = {
    item["function"]["name"]: item["function"].get("parameters") or {}
    for item in tools_schema.TOOLS_SCHEMA
    if item.get("type") == "function"
}


def validate_arguments(tool_name: str, kwargs) -> dict | None:
    """按 schema 校验参数；有问题返回 error 载荷，没问题返回 None。

    校验三件事：**是不是对象**、**必填项齐不齐**、**有没有幻觉参数**。
    不做类型校验——成本高、收益低，且多数工具自身能容忍。

    为什么不用 Python 的 ``TypeError`` 当错误信息：它（1）泄露实现细节，
    （2）不含"该怎么改"，（3）模型可能照抄那个措辞去回复用户。而 schema 里
    现成就有 ``required`` 与 ``properties``，直接拿来用即可。
    """
    if not isinstance(kwargs, dict):
        return {
            "error": f"参数必须是 JSON 对象，收到 {type(kwargs).__name__}",
            "error_kind": "bad_arguments",
            "retryable": True,
            "hint": '请输出形如 {"key": value} 的 JSON 对象。',
        }

    params = _TOOL_SPECS.get(tool_name) or {}
    required = list(params.get("required") or [])
    properties = params.get("properties") or {}

    missing = [name for name in required if name not in kwargs]
    if missing:
        return {
            "error": f"缺少必填参数: {', '.join(missing)}",
            "error_kind": "bad_arguments",
            "missing_arguments": missing,
            "required_arguments": required,
            "retryable": True,
        }

    unknown = [name for name in kwargs if name not in properties]
    if unknown:
        # 模型编了个参数名，通常意味着它对工具的理解有偏差。
        # 早点纠正，比让它带着错误理解继续往下走好。
        return {
            "error": f"不支持的参数: {', '.join(unknown)}",
            "error_kind": "bad_arguments",
            "unknown_arguments": unknown,
            "allowed_arguments": list(properties),
            "retryable": True,
        }

    return None


# ======================================================================
# 路由入口
# ======================================================================


def route(tool_name: str, arguments: str) -> str:
    """路由：解析名字 → 校验参数 → 执行 → 返回 JSON 字符串。

    任何失败都返回 ``{"error": ...}`` 而**不抛异常**：``agent.py`` 的循环
    依赖这一点，才能在工具出错后继续与模型对话（见 tool 消息回填）。

    错误载荷刻意带上 ``available_tools`` 与 ``did_you_mean``：模型看到清单后
    重新选择，比在没有新信息的情况下重采样有效得多——这正是 hermes-agent
    把"盲重试 3 次"改成"喂错误信息让模型自己改"的原因。

    ``retryable`` 用于区分两类失败：

    - ``True``：参数写错、名字近失 → 改一下立刻重试是对的；
    - ``False``：工具内部异常 → 大概率是确定性 bug 或依赖缺失，重试无用，
      模型应当改口告知用户，而不是反复重试烧光轮次。
    """
    resolved, how = resolve_tool_name(tool_name)

    if resolved is None:
        payload = {
            "error": f"未知工具: {tool_name}",
            "unknown_tool": True,
            "error_kind": "unknown_tool",
            "retryable": True,
            "did_you_mean": _suggest_tool_names(tool_name),
            "available_tools": sorted(TOOL_REGISTRY),
            "hint": "请从 available_tools 中选择工具名后重试，不要臆造工具名。",
        }
        print(f"[tools_route] ⚠️ 未知工具 tool={tool_name!r}")
        return json.dumps(payload, ensure_ascii=False)

    if how != "exact":
        # 修好了也要记：这是"本不该失败"的信号，值得单独统计
        print(f"[tools_route] 🔧 工具名已修复 tool={tool_name!r} -> {resolved!r} how={how}")

    try:
        kwargs = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as exc:
        return json.dumps(
            {
                "error": f"参数不是合法 JSON：{exc.msg}",
                "error_kind": "bad_json",
                "retryable": True,
                "hint": "请输出一个 JSON 对象作为参数。",
            },
            ensure_ascii=False,
        )

    problem = validate_arguments(resolved, kwargs)
    if problem is not None:
        return json.dumps(problem, ensure_ascii=False)

    try:
        return json.dumps(TOOL_REGISTRY[resolved](**kwargs), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        # 工具内部异常默认视为**不可重试**（大概率是确定性 bug 或依赖缺失）。
        # 真正的临时故障需要工具侧抛专门异常类型才能区分，属后续演进方向。
        print(
            f"[tools_route] ❌ 工具执行失败 tool={resolved!r} "
            f"exc={type(exc).__name__}: {exc}"
        )
        return json.dumps(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "error_kind": "tool_exception",
                "retryable": False,
                "hint": "该错误大概率无法通过重试解决，请改用其他方式回答或直接告知用户。",
            },
            ensure_ascii=False,
        )
