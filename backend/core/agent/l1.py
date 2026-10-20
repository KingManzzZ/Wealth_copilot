"""
L1 单次调用上下文管理。

## 作用域

一次 ``chat.completions.create`` 请求。L1 只关心"这一次的 messages 长什么样"：

- **稳定前缀**：system prompt 是模块常量，**不含日期/用户名**——任何动态内容都会让
  提示词缓存每轮/每天失效。日期改为注入到**当前 user 消息**（永远在最后，不影响前缀）。
- **工具结果截断**：单条 ``role="tool"`` 超过上限就保留头尾，避免一次大返回把窗口吃掉。
  与 L2 的"观察掩码"互补：L1 管"刚拿到的结果"，L2 管"变成历史之后"。
- **度量**：请求规模（system / tools / 历史 / 工具往返）与 ``usage``（可选）。

## 为什么 system 里不能放日期

OpenAI 的前缀缓存按"内容 + 它之前的所有内容"命中；system 一变，后面的 history/tools
全部失效。本项目原先在 system 里拼了"当前日期"，等于**每天**全量 miss 一次。
日期放到 user 消息里，前缀（system + tools）在整个会话内稳定。
"""

from __future__ import annotations

import json
import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[l1] ⚠️ 环境变量 {name}={raw!r} 不是整数，使用默认 {default}")
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


#: 单条工具结果进上下文前的字符上限（头 70% + 尾 30%）。
#: 取 4000：知识库检索 3~5 条证据通常在 2~4k；`include_parent=True` 才可能爆量。
TOOL_RESULT_MAX_CHARS = _env_int("L1_TOOL_RESULT_MAX_CHARS", 4000)

#: 是否请求流式响应携带 usage（OpenAI 兼容接口的 `stream_options.include_usage`）。
#: **默认关闭**：中转层不一定支持，开了可能直接 400；确认支持后再开。
INCLUDE_USAGE = _env_flag("L1_INCLUDE_USAGE", False)

TRUNCATION_MARKER = "\n…（工具结果过长，已截断：原 {chars} 字符，保留头尾）…\n"


# ---------------------------------------------------------------------------
# 工具结果截断
# ---------------------------------------------------------------------------


def truncate_tool_content(
    content: str, max_chars: int | None = None
) -> tuple[str, bool]:
    """超过上限时保留头尾；返回 ``(新内容, 是否截断)``。"""
    if max_chars is None:
        max_chars = TOOL_RESULT_MAX_CHARS
    if max_chars <= 0 or not isinstance(content, str) or len(content) <= max_chars:
        return content, False

    head = int(max_chars * 0.7)
    tail = max_chars - head
    marker = TRUNCATION_MARKER.format(chars=len(content))
    return content[:head] + marker + content[-tail:], True


def truncate_tool_messages(messages, max_chars: int | None = None) -> tuple[list, int]:
    """对一组协议消息里的 ``role="tool"`` 逐条截断；返回 ``(新列表, 截断条数)``。

    不修改入参；非 tool 消息原样保留（尤其是 assistant 的 ``tool_calls``）。
    """
    out = list(messages)
    count = 0
    for index, message in enumerate(out):
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        new_content, truncated = truncate_tool_content(content, max_chars)
        if truncated:
            replaced = dict(message)
            replaced["content"] = new_content
            out[index] = replaced
            count += 1
    return out, count


# ---------------------------------------------------------------------------
# 请求规模度量
# ---------------------------------------------------------------------------


def estimate_tools_chars(tools) -> int:
    """工具 schema 的固定成本（每轮都要传）。"""
    if not tools:
        return 0
    return len(json.dumps(tools, ensure_ascii=False, sort_keys=True))


def request_stats(messages, tools=None) -> dict:
    """按"段"统计本次请求的规模，供日志观测。"""
    system_chars = 0
    tool_chars = 0
    other_chars = 0
    for message in messages:
        size = len(str(message.get("content") or ""))
        role = message.get("role")
        if role == "system":
            system_chars += size
        elif role == "tool":
            tool_chars += size
        else:
            other_chars += size

    tools_chars = estimate_tools_chars(tools)
    return {
        "system_chars": system_chars,
        "tools_chars": tools_chars,
        "history_chars": other_chars,
        "tool_result_chars": tool_chars,
        "total_chars": system_chars + tools_chars + other_chars + tool_chars,
        "tool_count": len(tools) if tools else 0,
    }


def format_stats(stats: dict) -> str:
    return (
        f"system {stats['system_chars']} + tools {stats['tools_chars']}"
        f"（{stats['tool_count']} 个）+ 历史 {stats['history_chars']}"
        f" + 工具往返 {stats['tool_result_chars']}"
        f" = {stats['total_chars']} 字符"
    )


# ---------------------------------------------------------------------------
# usage（可选）
# ---------------------------------------------------------------------------


def usage_from_chunk(chunk) -> dict | None:
    """从流式 chunk 里取 usage；没有就返回 None（多数 chunk 都没有）。"""
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None

    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    if not prompt and not completion:
        return None

    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
    }


def log_usage(usage: dict, label: str = "") -> None:
    prompt = usage.get("prompt_tokens", 0)
    cached = usage.get("cached_tokens", 0)
    hit = (cached / prompt) if prompt else 0.0
    prefix = f"[l1] usage {label} " if label else "[l1] usage "
    print(
        f"{prefix}prompt={prompt} completion={usage.get('completion_tokens', 0)} "
        f"cached={cached} hit={hit:.0%}"
    )
