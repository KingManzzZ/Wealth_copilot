"""
历史回放：把 **UI 形态**的存储消息展开成 **OpenAI 协议形态**。

## 为什么需要这一层

``messages`` 表是为**前端折叠展示**设计的，不是为**模型回放**设计的：

- 只落 ``user`` / ``assistant`` 两种角色（``services/conversations.py`` 的
  ``append_messages`` 仅接受这两种）；
- 工具结果、状态内嵌在 ``assistant.tool_calls`` 这个 JSON 列里（UI 形态）。

而 OpenAI 协议要求：

- ``arguments`` 是 **JSON 字符串**（不是对象）；
- 结果放在**独立的** ``role="tool"`` 消息里；
- ``tool_calls`` 元素形如 ``{id, type:"function", function:{name, arguments}}``。

直接回放 UI 形态会缺 ``role="tool"`` 消息 → 协议校验失败（400）。

## 四条不变量（缺一个就 400 或语义错乱）

===============  ==============================================================
I1 配对完整      每个 ``tool_calls[i].id`` **恰好**对应一条 ``tool`` 消息
I2 无孤儿        每条 ``tool`` 消息的 ``tool_call_id`` 必须能在**紧邻的前一条**
                 assistant 的 ``tool_calls`` 里找到
I3 类型正确      ``arguments`` 必须是 JSON 字符串
I4 缺失必补      没有 ``result`` 时**合成**一条"未完成"提示，不能省略该消息
===============  ==============================================================

**I1 与 I2 必须由同一个过滤后的列表驱动。** 两侧分别过滤会产生孤儿消息——
前端旧实现（``useChat.ts`` 的 ``buildHistory``）踩过这个坑：``tool_calls``
用 ``filter(tc => tc.name)``，tool 消息却遍历未过滤的 ``calls``。

**I4 是把致命错误降级为可恢复提示的关键**：省略 → 缺应答 → 该会话永久无法续聊；
合成 → 模型看到"这个调用没拿到结果，可以重试" → 会话活着。

> 与 ``agent.py`` 里"重复调用跳过时仍要回填 tool 消息"是同一条协议规则的另一次体现
> （见 ``问题文档/Agent-工具调用可靠性.md`` 的 T2-1）。
"""

import json

#: 给"没有结果"的工具调用合成的提示。
#: 刻意复用 ``tools_route.route()`` 的错误契约（error_kind / retryable），
#: 让模型用同一套理解处理——它已经学会"retryable=true 就重试"。
INTERRUPTED_PAYLOAD = {
    "error": "该工具调用未返回结果（可能被中断）",
    "error_kind": "interrupted",
    "retryable": True,
    "hint": "如需该数据，请重新调用此工具。",
}

INTERRUPTED_CONTENT = json.dumps(INTERRUPTED_PAYLOAD, ensure_ascii=False)


def _arguments_to_string(value) -> str:
    """I3：协议要求 arguments 是 JSON 字符串；存储里可能是对象，也可能是字符串。"""
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def expand_to_model_messages(rows) -> list:
    """把存储形态的消息行展开成协议形态的 messages（**不含 system**）。

    ``rows`` 元素：``{"role": "user"|"assistant", "content": str, "tool_calls": [...]?}``
    其中 ``tool_calls`` 元素是 UI 形态：
    ``{"id", "name", "arguments": dict, "result"?, "status"?}``

    **只做形态转换，不做裁剪。** 裁剪（掩码 / 窗口 / 摘要）是下游 ``agent/context.py``
    的职责，两者分开才能各自测试、各自回滚。
    """
    out: list = []

    for row in rows:
        role = row.get("role")

        if role != "assistant":
            out.append({"role": role, "content": row.get("content") or ""})
            continue

        calls = row.get("tool_calls") or []

        # I1/I2 的实现关键：**只在这里过滤一次**，tool_calls 与 tool 消息共用 usable。
        # 绝不能让两侧各自过滤——那正是孤儿消息的成因。
        usable = [c for c in calls if c.get("id") and c.get("name")]
        if len(usable) != len(calls):
            print(
                f"[replay] ⚠️ 丢弃 {len(calls) - len(usable)} 条无 id/name 的 tool_call"
                f"（无法构造合法协议，成对丢弃）"
            )

        if not usable:
            out.append({"role": "assistant", "content": row.get("content") or ""})
            continue

        out.append(
            {
                "role": "assistant",
                "content": row.get("content") or "",
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": _arguments_to_string(c.get("arguments")),
                        },
                    }
                    for c in usable
                ],
            }
        )

        # I4：**无条件**为每个 usable 补一条 tool 消息；缺 result 时用合成内容。
        for c in usable:
            result = c.get("result")
            if result is None:
                content = INTERRUPTED_CONTENT
            elif isinstance(result, str):
                content = result
            else:
                content = json.dumps(result, ensure_ascii=False)
            out.append({"role": "tool", "tool_call_id": c["id"], "content": content})

    return out


def _looks_like_protocol(messages) -> bool:
    """判断一批消息是否已经是 OpenAI 协议形态（而非 UI 存储形态）。

    判据：存在 ``role="tool"`` 消息，或任意 ``tool_calls`` 元素带 ``function`` 键。
    """
    for m in messages:
        if not isinstance(m, dict):
            return False
        if m.get("role") == "tool":
            return True
        for c in m.get("tool_calls") or []:
            if isinstance(c, dict) and "function" in c:
                return True
    return False


def normalize_history(messages) -> list:
    """兼容期用：已经是协议形态就原样返回，是 UI 形态就展开。

    幂等——同一个输入反复调用结果一致。仅用于**旧前端仍传 history** 的降级路径；
    服务端权威路径（带 ``conversation_id``）不经过这里。
    """
    if not messages:
        return []
    if _looks_like_protocol(messages):
        return list(messages)
    return expand_to_model_messages(messages)
