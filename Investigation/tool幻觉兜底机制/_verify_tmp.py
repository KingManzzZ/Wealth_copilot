"""兜底策略自检脚本 —— 在改动生产代码前先验证骨架行为。

作用：把 [02-落地方案与代码骨架.md](02-落地方案与代码骨架.md) 第 1 章的
名称归一化 / 分档模糊匹配 / 参数校验逻辑，用本项目**真实的 24 个工具名**
跑一遍，确认行为与文档描述一致，再往 `core/tools/tools_route.py` 里搬。

为什么值得单独跑一次：模糊匹配的阈值很反直觉（详见骨架文档 1.4 节），
"看起来该命中"的不会命中、"看起来是噪声"的反而相似度更高。先在这里确认，
比在线上发现 `update_asset` 被改成了 `update_debt` 便宜得多。

用法::

    python "_verify_tmp.py"        # 全部通过则退出码 0

注意：文件名是创建时的遗留（原为临时验证脚本，创建后受沙箱限制无法删除或改名），
不影响执行；如方便，可手动重命名为 `自检脚本-兜底策略验证.py`。
"""
import difflib
import json
import re

# ======================================================================
# 替身：用真实工具名 + 精简后的 schema 片段
# ======================================================================

TOOL_REGISTRY = {
    n: (lambda **kw: {"ok": n, "args": kw})
    for n in [
        "get_financial_profile", "update_basic_info", "get_assets", "record_asset",
        "update_asset", "calculate_net_worth", "calculate_emergency_fund", "get_debts",
        "record_debt", "update_debt", "create_goal", "get_goals", "update_goal",
        "complete_goal_tool", "calculate_goal_contribution", "record_income",
        "record_expense", "get_transactions", "update_transaction_tool",
        "delete_transaction_tool", "get_monthly_cashflow", "calculate_savings_rate",
        "search_knowledge", "request_withdraw", "remember_note", "forget_note",
    ]
}

_TOOL_SPECS = {
    "get_assets": {"type": "object", "properties": {}, "required": []},
    "search_knowledge": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "top_k": {"type": "number"}},
        "required": ["query"],
    },
}

# ======================================================================
# 骨架实现（与 02-落地方案 第 1 章一致）
# ======================================================================

_READ_ONLY_PREFIXES = ("get_", "calculate_", "search_", "list_")
_AUTO_REPAIR_CUTOFF = 0.85
_SUGGEST_CUTOFF = 0.5
_SUGGEST_LIMIT = 3

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEP_RE = re.compile(r"[\s\-./:]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_NAMESPACE_RE = re.compile(r"^(?:functions?|tools?)\s*[.:]\s*", re.IGNORECASE)


def normalize_name(name: str) -> str:
    s = str(name or "").strip()
    s = _NAMESPACE_RE.sub("", s)
    s = _CAMEL_RE.sub("_", s)
    s = s.lower()
    s = _SEP_RE.sub("_", s)
    return _MULTI_UNDERSCORE_RE.sub("_", s).strip("_")


_NORMALIZED: dict = {}


def _rebuild_normalized_index() -> None:
    _NORMALIZED.clear()
    for name in TOOL_REGISTRY:
        _NORMALIZED.setdefault(normalize_name(name), name)


def _is_read_only(tool_name: str) -> bool:
    return tool_name.startswith(_READ_ONLY_PREFIXES)


def resolve_tool_name(tool_name: str) -> tuple:
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
    matches = difflib.get_close_matches(
        normalize_name(tool_name), _NORMALIZED.keys(), n=_SUGGEST_LIMIT, cutoff=_SUGGEST_CUTOFF
    )
    return [_NORMALIZED[m] for m in matches]


def validate_arguments(tool_name: str, kwargs) -> dict | None:
    if not isinstance(kwargs, dict):
        return {
            "error": f"参数必须是 JSON 对象，收到 {type(kwargs).__name__}",
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
            "missing_arguments": missing,
            "required_arguments": required,
            "retryable": True,
        }
    unknown = [name for name in kwargs if name not in properties]
    if unknown:
        return {
            "error": f"不支持的参数: {', '.join(unknown)}",
            "unknown_arguments": unknown,
            "allowed_arguments": list(properties),
            "retryable": True,
        }
    return None


_rebuild_normalized_index()


def route(tool_name: str, arguments: str) -> str:
    resolved, how = resolve_tool_name(tool_name)
    if resolved is None:
        payload = {
            "error": f"未知工具: {tool_name}",
            "unknown_tool": True,
            "retryable": True,
            "did_you_mean": _suggest_tool_names(tool_name),
            "available_tools": sorted(TOOL_REGISTRY),
            "hint": "请从 available_tools 中选择工具名后重试，不要臆造工具名。",
        }
        return json.dumps(payload, ensure_ascii=False)
    try:
        kwargs = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as exc:
        return json.dumps(
            {"error": f"参数不是合法 JSON：{exc.msg}", "error_kind": "bad_json", "retryable": True},
            ensure_ascii=False,
        )
    problem = validate_arguments(resolved, kwargs)
    if problem is not None:
        problem["error_kind"] = "bad_arguments"
        return json.dumps(problem, ensure_ascii=False)
    try:
        return json.dumps(TOOL_REGISTRY[resolved](**kwargs), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "error_kind": "tool_exception",
                "retryable": False,
            },
            ensure_ascii=False,
        )


# ======================================================================
# 断言
# ======================================================================

_FAILED = []


def check(label, cond):
    print(("  [ok]   " if cond else "  [FAIL] ") + label)
    if not cond:
        _FAILED.append(label)


def main() -> int:
    r = lambda n, a="{}": json.loads(route(n, a))

    print("[1] 名称归一化：四种书写漂移都必须命中")
    for v in ("Get_Assets", "get-assets", "getAssets", "functions.get_assets"):
        check(f"{v} 不报未知工具", "未知工具" not in r(v).get("error", ""))
        check(f"{v} 命中方式=normalized", resolve_tool_name(v)[1] == "normalized")

    print("[2] 只读工具近失：可以自动修复")
    check("get_asset -> get_assets/fuzzy", resolve_tool_name("get_asset") == ("get_assets", "fuzzy"))

    print("[3] 写工具近失：绝不能自动修复（安全底线）")
    for v in ("update_asst", "record_asets", "delete_transaction"):
        res, _ = resolve_tool_name(v)
        check(f"{v} 未被自动修复", res is None)

    print("[4] 未知工具的错误载荷必须可操作")
    p = r("search_policy")
    check("unknown_tool=True", p.get("unknown_tool") is True)
    check("有 did_you_mean", bool(p.get("did_you_mean")))
    check("有 available_tools", len(p.get("available_tools") or []) == 26)
    check("有 hint", bool(p.get("hint")))

    print("[5] 参数校验：用 schema 而不是 Python 报错")
    p = r("search_knowledge", "{}")
    check("缺 query", p.get("missing_arguments") == ["query"] and p.get("retryable") is True)
    p = r("get_assets", '{"foo": 1}')
    check("幻觉参数被拒", p.get("unknown_arguments") == ["foo"])
    for raw in ("[1,2]", "null", '"str"'):
        check(f"{raw} 被拒", "必须是 JSON 对象" in r("get_assets", raw).get("error", ""))
    check("非法 JSON 被拒", r("search_knowledge", "{not json").get("error_kind") == "bad_json")

    print()
    if _FAILED:
        print(f"{len(_FAILED)} 项失败：")
        for label in _FAILED:
            print(f"  - {label}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
