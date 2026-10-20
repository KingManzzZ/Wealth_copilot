"""
L2 上下文压缩（compaction）。

## 它解决什么

掩码 + 滑动窗口只能"少放一些"，会话一长，token 仍然单调上升。压缩做的是
**把较早的轮次改写成一段结构化摘要**，之后只发摘要 + 最近几轮原文。

## 三条硬规则（来自 ``Investigation/上下文机制/02-L2-单会话上下文.md``）

1. **只在轮次边界压缩**：本模块只在"准备这一轮历史"时调用，绝不插进工具循环中间
   （那会抹掉"刚查到什么"，直接毁掉当前推理）。
2. **锚点不参与摘要**：会话第一条用户消息（原始目标）永远原文保留，
   否则多层压缩后目标会漂移。
3. **压缩本身也要吃 token**：所以触发阈值是预算的 75%，留出余量，而不是等到 99%。

## 流程

``maybe_compact()``：
读状态 → 用"当前已组装历史的规模"判断是否过阈值 → 划出可压缩区间
（锚点之后、最近 ``KEEP_RECENT_TURNS`` 轮之前）→ 生成 transcript →
调摘要模型 → 写 ``conversations.summary`` + ``summary_until_message_id`` +
``compactions`` 审计行。

之后 ``context.prepare_history(rows, state=...)`` 只发：
**锚点原文 + 一条摘要 system 消息 + 未被摘要覆盖的最近消息**。

## 配置

- ``CONTEXT_COMPACTION=0`` 关闭（默认开启）
- ``CONTEXT_COMPACT_AT`` 触发阈值，默认 0.75
- ``CONTEXT_KEEP_RECENT_TURNS`` 压缩后保留最近多少轮原文，默认 6

## 已知取舍

增量摘要（"已有摘要 + 新增对话"）而不是每次从原文重算：成本可控，但有递归漂移风险。
缓解手段是锚点独立 + 每次压缩都落 ``compactions``（保留历次摘要，可回溯/重算）。
"""

from __future__ import annotations

import os
from datetime import datetime

from ..database import get_connection
from . import context

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[compaction] ⚠️ 环境变量 {name}={raw!r} 不是整数，使用默认 {default}")
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[compaction] ⚠️ 环境变量 {name}={raw!r} 不是小数，使用默认 {default}")
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


COMPACT_AT = _env_float("CONTEXT_COMPACT_AT", 0.75)
KEEP_RECENT_TURNS = _env_int("CONTEXT_KEEP_RECENT_TURNS", 6)
ENABLED = _env_flag("CONTEXT_COMPACTION", True)

PROMPT_VERSION = "compact-v1"

#: 单条消息进摘要前的截断长度——工具返回可能上万字，原样喂给摘要模型既贵又没用。
MAX_CONTENT_CHARS = 900

#: transcript 总长上限。超出时保留头尾（开头是目标、结尾是最近进展）。
MAX_TRANSCRIPT_CHARS = 24000

#: 摘要 system 消息的前缀（与 ``context`` 共用，避免两处不一致）。
SUMMARY_PREFIX = context.SUMMARY_PREFIX

SUMMARY_SYSTEM_PROMPT = """你是对话压缩器。请把下面的对话压缩成一份**结构化摘要**，供后续推理使用。

必须保留（逐条列出，不要概括、不要合并、不要取整）：
1. 用户的原始目标与所有约束
2. 已确认的事实与数值（金额、日期、比例等保持精确）
3. 做过的决策及其理由
4. 未解决的问题 / 待办
5. 试过但失败的路径（各一句话，说明为什么失败）

可以丢弃：冗长的工具返回原文、重复的寒暄、已经得出结论的中间讨论。

输出用 Markdown 分节，控制在 400 字以内。不要编造对话里没有的信息。"""


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def load_state(conversation_id: int) -> dict:
    """读会话的压缩状态；不存在时返回空状态（不抛错，压缩不该阻断聊天）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT summary, summary_until_message_id, summary_anchor_message_id
            FROM conversations WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"summary": None, "until": None, "anchor": None}
    return {
        "summary": row["summary"],
        "until": row["summary_until_message_id"],
        "anchor": row["summary_anchor_message_id"],
    }


def should_trigger(tokens: int, budget: int | None = None) -> bool:
    """是否达到压缩阈值（默认 75%）。"""
    if budget is None:
        budget = context.CONTEXT_BUDGET_TOKENS
    return tokens >= COMPACT_AT * budget


# ---------------------------------------------------------------------------
# 划界
# ---------------------------------------------------------------------------


def plan(rows, state: dict | None = None, keep_recent: int | None = None) -> dict | None:
    """划出"要压缩哪一段"。

    - 锚点 = 会话第一条用户消息（已有状态时沿用已记录的锚点）；
    - 保留最近 ``keep_recent`` 轮（按 user 消息计）原文；
    - 可压缩区间 = 锚点之后、保留边界之前、且未被上次摘要覆盖。

    返回 ``{anchor, scope, covered_from, covered_until, keep_from}``；不够压缩时返回 ``None``。
    """
    if keep_recent is None:
        keep_recent = KEEP_RECENT_TURNS
    if state is None:
        state = {"summary": None, "until": None, "anchor": None}

    user_rows = [r for r in rows if r["role"] == "user"]
    if len(user_rows) <= keep_recent:
        return None

    anchor = state.get("anchor") or user_rows[0]["id"]
    boundary = user_rows[-keep_recent]["id"]
    if boundary <= anchor:
        return None

    start_after = state.get("until") or anchor
    scope = [
        r
        for r in rows
        if anchor < r["id"] < boundary and r["id"] > start_after
    ]
    if not scope:
        return None

    return {
        "anchor": anchor,
        "scope": scope,
        "covered_from": min(r["id"] for r in scope),
        "covered_until": max(r["id"] for r in scope),
        "keep_from": boundary,
    }


# ---------------------------------------------------------------------------
# 生成摘要
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（截断，原长 {len(text)} 字符）"


def _tool_text(result) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return _clip(result, 400)
    try:
        import json

        return _clip(json.dumps(result, ensure_ascii=False), 400)
    except (TypeError, ValueError):
        return _clip(str(result), 400)


def build_transcript(scope, previous_summary: str | None = None) -> str:
    """把待压缩的行拼成给摘要模型的文本（工具结果会截断）。"""
    lines: list[str] = []
    if previous_summary:
        lines.append("【已有摘要（需要与新对话合并）】")
        lines.append(previous_summary.strip())
        lines.append("")

    for row in scope:
        role = row.get("role")
        content = row.get("content") or ""
        if role == "assistant":
            parts = []
            for call in row.get("tool_calls") or []:
                name = call.get("name") or "unknown"
                result = _tool_text(call.get("result"))
                if result:
                    parts.append(f"[工具 {name}] {result}")
            if parts:
                content = (content + "\n" if content else "") + "\n".join(parts)
            label = "助手"
        elif role == "user":
            label = "用户"
        else:
            label = "系统"
        lines.append(f"{label}：{_clip(str(content))}")

    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        half = MAX_TRANSCRIPT_CHARS // 2
        text = text[:half] + "\n…（中间过长部分已省略）…\n" + text[-half:]
    return text


def _default_summarize(transcript: str) -> str:
    """调真实摘要模型（非流式，温度 0.2）。"""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_URL"),
    )
    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def maybe_compact(
    conversation_id: int,
    rows,
    state: dict | None = None,
    current_tokens: int | None = None,
    budget: int | None = None,
    summarize=None,
    enabled: bool | None = None,
    force: bool = False,
) -> dict | None:
    """按阈值决定是否压缩；压缩则落库并返回摘要信息，否则返回 ``None``。

    - ``current_tokens``：``context.prepare_history`` 给出的 ``pre_tokens``
      （裁剪前规模）。不给就现算一次；
    - ``summarize``：可注入（测试用），默认调真实模型；
    - ``enabled`` / ``force``：测试与运维开关（``force`` 忽略阈值）。
    """
    if enabled is None:
        enabled = ENABLED
    if not enabled:
        return None
    if budget is None:
        budget = context.CONTEXT_BUDGET_TOKENS
    if state is None:
        state = load_state(conversation_id)
    if current_tokens is None:
        current_tokens = context.prepare_history(rows, budget=budget, state=state)[1][
            "pre_tokens"
        ]
    if not force and not should_trigger(current_tokens, budget):
        return None

    plan_result = plan(rows, state)
    if plan_result is None:
        return None

    transcript = build_transcript(plan_result["scope"], state.get("summary"))
    summary = (summarize or _default_summarize)(transcript).strip()
    if not summary:
        print("[compaction] ⚠️ 摘要为空，放弃这次压缩")
        return None

    kept = [r for r in rows if r["id"] >= plan_result["keep_from"]]
    after_tokens = context.estimate_tokens(
        [{"role": "system", "content": summary}]
    ) + sum(len(str(r.get("content") or "")) for r in kept)

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE conversations
            SET summary = ?, summary_until_message_id = ?,
                summary_anchor_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                summary,
                plan_result["covered_until"],
                plan_result["anchor"],
                _now(),
                conversation_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO compactions
                (conversation_id, covered_from_message_id, covered_until_message_id,
                 before_tokens, after_tokens, summary, prompt_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                plan_result["covered_from"],
                plan_result["covered_until"],
                current_tokens,
                after_tokens,
                summary,
                PROMPT_VERSION,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(
        f"[compaction] 会话 {conversation_id} 已压缩："
        f"消息 {plan_result['covered_from']}~{plan_result['covered_until']}，"
        f"估算 {current_tokens} → {after_tokens}"
    )
    return {
        "summary": summary,
        "covered_from": plan_result["covered_from"],
        "covered_until": plan_result["covered_until"],
        "anchor": plan_result["anchor"],
        "keep_from": plan_result["keep_from"],
        "before_tokens": current_tokens,
        "after_tokens": after_tokens,
    }
