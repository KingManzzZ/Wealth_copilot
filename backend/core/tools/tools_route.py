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
"""

import importlib
import inspect
import os
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


def route(tool_name: str, arguments: str) -> str:
    """
    路由函数：根据工具名查找注册表 -> 解析参数 -> 执行 -> 返回 JSON 字符串。
    """
    import json

    func = TOOL_REGISTRY.get(tool_name)
    if func is None:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    try:
        kwargs = json.loads(arguments)
        result = func(**kwargs)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
