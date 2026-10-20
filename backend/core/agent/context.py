"""
上下文预算与裁剪（L1 截断 + L2 观察掩码的最小实现）。

## 为什么先做掩码，而不是摘要

JetBrains 的实测（arXiv 2508.21433，SWE-bench Verified，5 种模型配置）：
**环境观察（工具输出）约占一个典型 turn 的 84%**；而"观察掩码"（把旧工具输出
换成占位符）能以 **−50%~−57% 的成本** 拿到与 LLM 摘要持平、有时更好的 solve rate。

而 LLM 摘要有反例：Gemini 2.5 Flash（thinking）上，摘要把 solve rate 从
40.4% 打到 **31.4%（−22.3%）**。

所以顺序是：**掩码 → 滑动窗口 → 最后才摘要**。本模块做前两步；
第三步（compaction）在 ``core/agent/compaction.py``，由 ``prepare_history(state=...)``
注入摘要，两者配合使用。

## 两个硬约束

1. **只掩码工具输出，不动 assistant 的 tool_calls。**
   模型仍然知道"我在第 3 步用 query=X 查过知识库"，只是看不到那 15k 原文——
   既省 token，又不破坏"我做过什么"的连续性。

2. **裁剪只能在「单元」边界发生。**
   assistant 与其后的 tool 消息是一个不可拆分的配对单元；拆开就会缺 tool 应答 → 400。

参考：``Investigation/上下文机制/02-L2-单会话上下文.md``
"""

import os

from ..services.replay import expand_to_model_messages

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """读整数环境变量；非法值降级为默认（配置不该让服务起不来）。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[context] ⚠️ 环境变量 {name}={raw!r} 不是整数，使用默认 {default}")
        return default


#: 上下文预算（**字符数上界**，粗估 token；见 ``estimate_tokens``）。
#: 取 8000 的理由：财务咨询单轮有效历史通常 2~4k token，8000 已有明显余量；
#: 再大的窗口对质量没有帮助，反而触发 context rot（注意力预算被摊薄）。
CONTEXT_BUDGET_TOKENS = _env_int("CONTEXT_BUDGET_TOKENS", 8000)

#: 保留最近多少条工具输出**原样**，更早的替换成占位符。
#: 取 8：一次财务咨询通常 2~4 轮工具调用（可能并行），8 足以覆盖"当前正在处理的任务"。
KEEP_RECENT_TOOL_RESULTS = _env_int("KEEP_RECENT_TOOL_RESULTS", 8)

#: 掩码后写回的内容。要**可读且可操作**——告诉模型"这里原本有内容、能重新取"，
#: 而不是留空字符串（那会让模型以为工具什么都没返回）。
MASKED_PLACEHOLDER = "[较早的工具输出已省略；如需重新获取，请再次调用该工具]"

#: 压缩后注入的 system 消息前缀（compaction.py 复用）。
SUMMARY_PREFIX = "【早前对话摘要（已压缩，原文不再逐条发送）】\n"


# ---------------------------------------------------------------------------
# 度量
# ---------------------------------------------------------------------------


def estimate_tokens(messages) -> int:
    """粗略估算上下文规模（按字符数计）。

    刻意**高估**——预算宁可提前触发裁剪，也不要真的撞窗口。
    中文约 1 字/token，英文约 4 字符/token，统一按 1 字符 ≈ 1 token 处理是安全上界。

    真正的 token 数只能靠 ``usage`` 拿到，但那是**响应之后**才有的；
    而裁剪必须在**请求之前**决定，所以这里只能估算。
    """
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            total += len(str(fn.get("name") or ""))
            total += len(str(fn.get("arguments") or ""))
    return total


# ---------------------------------------------------------------------------
# 观察掩码（L2 最轻的一档）
# ---------------------------------------------------------------------------


def mask_old_tool_results(messages, keep: int = None) -> tuple:
    """把较早的 ``role="tool"`` 消息内容替换成占位符。

    返回 ``(新列表, 被掩码条数)``。**不修改入参**（返回浅拷贝）。

    三条必须守住的点：

    1. **只清 content，不删消息**——``tool_call_id`` 必须还在，否则配对断裂；
    2. **不掩码最近的 keep 条**——当前任务正在用的结果不能动；
    3. **占位符要可读**——见 ``MASKED_PLACEHOLDER``。
    """
    if keep is None:
        keep = KEEP_RECENT_TOOL_RESULTS

    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idx) <= keep:
        return list(messages), 0

    out = list(messages)
    targets = tool_idx[:-keep] if keep > 0 else tool_idx
    for i in targets:
        masked = dict(out[i])
        masked["content"] = MASKED_PLACEHOLDER
        out[i] = masked
    return out, len(targets)


# ---------------------------------------------------------------------------
# 滑动窗口（L2 次轻的一档）
# ---------------------------------------------------------------------------


def _group_units(messages) -> list:
    """把消息按"不可拆分单元"分组，供裁剪使用。

    - ``user`` 单独一组；
    - ``assistant`` + 紧随其后的 N 条 ``tool`` 为一组（配对不可拆）；
    - 孤儿 ``tool``（理论上不该出现）单独成组，避免污染相邻单元。

    **裁剪只能在单元边界发生**——拆开 assistant/tool 配对会直接导致 400。
    """
    units: list = []
    current = None

    for m in messages:
        role = m.get("role")
        if role == "user":
            units.append([m])
            current = None
        elif role == "assistant":
            current = [m]
            units.append(current)
        elif role == "tool":
            if current is None:
                units.append([m])  # 孤儿，保守处理
            else:
                current.append(m)
        else:
            # system 等其它角色单独成组，不参与配对
            units.append([m])
            current = None

    return units


def trim_to_budget(messages, budget: int, keep_first: int = 1) -> tuple:
    """从**最旧**的非保留单元开始丢弃，直到落在预算内。

    返回 ``(新列表, 丢弃的单元数)``。

    ``keep_first``：保留最前面几个单元不参与丢弃。默认 1 ——
    **保护会话开头的用户原始目标**（"5 年内攒够 50 万""不接受股票"）。
    丢了它，模型会给出方向错误的建议，而这正是"聊着聊着忘了最初要干嘛"的技术根因。

    > 有压缩摘要时，调用方传 ``keep_first=2``：锚点 + 摘要两条都要保住，
    > 否则"摘要还在、锚点被裁"等于把原始目标弄丢了。
    """
    units = _group_units(messages)

    def flatten(groups) -> list:
        return [m for g in groups for m in g]

    if estimate_tokens(messages) <= budget:
        return list(messages), 0

    protected = max(keep_first, 1)  # 至少留 1 个单元，绝不裁空
    dropped = 0
    while len(units) > protected and estimate_tokens(flatten(units)) > budget:
        units.pop(protected)  # 丢掉最旧的非保留单元
        dropped += 1

    return flatten(units), dropped


# ---------------------------------------------------------------------------
# 组装入口
# ---------------------------------------------------------------------------


def _compose_messages(rows, state) -> tuple:
    """按压缩状态组装消息：锚点原文 + 摘要 + 未被覆盖的最近消息。"""
    if not state or not state.get("summary") or state.get("until") is None:
        return expand_to_model_messages(rows), False

    anchor = state.get("anchor")
    until = state["until"]
    kept = [
        row
        for row in rows
        if (anchor is not None and row["id"] <= anchor) or row["id"] > until
    ]
    anchor_rows = [
        row for row in kept if anchor is not None and row["id"] <= anchor
    ]
    recent_rows = [row for row in kept if row["id"] > until]

    messages = expand_to_model_messages(anchor_rows)
    messages.append(
        {"role": "system", "content": SUMMARY_PREFIX + str(state["summary"])}
    )
    messages.extend(expand_to_model_messages(recent_rows))
    return messages, True


def prepare_history(rows, budget: int = None, state: dict = None) -> tuple:
    """把存储行变成"可发给模型的 history"，并返回度量信息。

    **``rows`` 不得包含本轮的用户消息**——``chat_stream`` 会自己追加。
    调用方应先读库、再调本函数、最后才落库本轮用户消息（顺序见 ``api/routes/chat.py``）。

    ``state`` 是压缩状态（``compaction.load_state`` 的返回）：
    - ``summary`` / ``until`` / ``anchor`` 齐全时，只发"锚点 + 摘要 + 最近消息"，
      并把 ``keep_first`` 提到 2，确保锚点与摘要都不会被窗口裁掉。

    返回 ``(history, info)``，``info`` 供日志观测：

    - ``messages``：原始行数
    - ``masked``：被掩码的工具输出条数
    - ``dropped_units``：因超预算被丢弃的单元数
    - ``pre_tokens``：掩码后、裁剪前的规模（压缩触发判断用它）
    - ``tokens``：裁剪后的估算规模
    - ``budget``：本次生效的预算
    - ``compacted``：是否有摘要参与组装
    """
    if budget is None:
        budget = CONTEXT_BUDGET_TOKENS

    messages, compacted = _compose_messages(rows, state)
    masked_messages, masked_count = mask_old_tool_results(messages)
    pre_tokens = estimate_tokens(masked_messages)
    trimmed, dropped = trim_to_budget(
        masked_messages, budget, keep_first=2 if compacted else 1
    )

    info = {
        "rows": len(rows),
        "masked": masked_count,
        "dropped_units": dropped,
        "pre_tokens": pre_tokens,
        "tokens": estimate_tokens(trimmed),
        "budget": budget,
        "compacted": compacted,
    }
    return trimmed, info
