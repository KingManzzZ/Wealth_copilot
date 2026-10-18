"""`request_withdraw` 提案工具：只读、目标解析、错误分支、契约注册。

这一层不经过 LLM，直接调用工具函数（工具本身是纯逻辑 + 一次只读查询）。
"""

import _support
from run_tests import eq, in_, true

from core.agent import turn_context
from core.services import conversations as conv
from core.services.transactions import add_transaction, list_transactions
from core.tools import tools_route, tools_schema
from core.tools.tools_hub.withdraw_tools import request_withdraw


def check_tool_is_registered():
    names = [item["function"]["name"] for item in tools_schema.TOOLS_SCHEMA]
    in_("request_withdraw", names, "schema 里应有 request_withdraw")
    in_("request_withdraw", tools_route.TOOL_REGISTRY, "注册表里应有 request_withdraw")


def check_proposal_targets_previous_turn_and_changes_nothing():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid, "t1", "记一笔 10", write=lambda: add_transaction("expense", 10, "餐饮", "2026-10-18T10:00:00")
        )
        _support.run_turn(
            cid, "t2", "再记一笔 20", write=lambda: add_transaction("expense", 20, "交通", "2026-10-18T11:00:00")
        )
        messages_before = len(conv.get_conversation_messages(cid))
        tx_before = len(list_transactions())

        turn_context.bind(cid, "t2")  # 当前这一轮是 t2，默认应指向上一轮 t1
        try:
            payload = request_withdraw()
        finally:
            turn_context.clear()

        eq(payload.get("proposal"), "withdraw_turn", "应返回提案")
        eq(payload.get("turn_id"), "t1", "默认撤回上一轮")
        eq(payload.get("requires_confirmation"), True, "必须要求用户确认")
        eq(payload.get("affected_turns"), 1, "只影响一轮")
        eq(payload.get("affected_writes"), 1, "该轮有一处写入")
        eq(payload.get("conversation_id"), cid, "应带上会话 id")
        true("确认" in (payload.get("notice") or ""), "应提醒模型让用户确认")

        # 只读：消息与数据都不变
        eq(len(conv.get_conversation_messages(cid)), messages_before, "不得修改消息")
        eq(len(list_transactions()), tx_before, "不得修改数据")


def check_explicit_turn_id_is_honored():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t3", "第一轮")
        _support.run_turn(cid, "t4", "第二轮")

        turn_context.bind(cid, "t4")
        try:
            payload = request_withdraw(turn_id="t3")
        finally:
            turn_context.clear()

        eq(payload.get("turn_id"), "t3", "应使用显式传入的 turn_id")
        eq(payload.get("affected_turns"), 1, "提案只针对这一轮（不截断后面的对话）")


def check_first_turn_has_no_previous():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "only", "唯一一轮")

        turn_context.bind(cid, "only")
        try:
            payload = request_withdraw()
        finally:
            turn_context.clear()

        eq(payload.get("error_kind"), "no_withdrawable_turn", "没有上一轮时应报错")
        true("error" in payload, "应带 error 字段（保持错误契约）")


def check_unknown_turn_returns_error():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t5", "一轮")

        turn_context.bind(cid, "t5")
        try:
            payload = request_withdraw(turn_id="does-not-exist")
        finally:
            turn_context.clear()

        eq(payload.get("error_kind"), "turn_not_found", "未知轮次应报 turn_not_found")


def check_without_turn_context_returns_error():
    turn_context.clear()
    payload = request_withdraw()
    eq(payload.get("error_kind"), "no_turn_context", "不在轮次内应报错")
