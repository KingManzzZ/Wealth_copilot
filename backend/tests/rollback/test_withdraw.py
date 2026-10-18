"""撤回一轮的服务层行为：写集差异、还原、冲突、范围。

这一层不经过 HTTP，直接验证 `turn_state` + `conversations` 的配合。
路由层见 `test_withdraw_route.py`。
"""

import _support
from run_tests import eq, in_, not_in, raises, true

from core.database import get_connection
from core.services import conversations as conv
from core.services import turn_state
from core.services.assets import list_assets, upsert_asset
from core.services.transactions import (
    add_transaction,
    delete_transaction,
    list_transactions,
)


def check_insert_is_reverted_and_messages_disappear():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid,
            "t1",
            "记一笔午餐 35",
            write=lambda: add_transaction("expense", 35, "餐饮", "2026-10-18T12:00:00"),
            assistant_text="已记录",
        )
        eq(len(list_transactions()), 1, "写入后应有一条流水")
        eq(len(conv.get_conversation_messages(cid)), 2, "应有两条消息")

        result = turn_state.withdraw(cid, "t1")

        eq(list_transactions(), [], "流水应被删除")
        eq(conv.get_conversation_messages(cid), [], "消息应不可见")
        eq(result["withdrawn_messages"], 2, "应撤回两条消息")
        eq(result["user_text"], "记一笔午餐 35", "应返回用户原话（回填输入框）")
        eq(result["rollback"]["compensated"], 1, "应撤销一条写")


def check_no_writes_turn_can_be_withdrawn():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t2", "你好", assistant_text="你好呀")

        result = turn_state.withdraw(cid, "t2")

        eq(result["rollback"]["compensated"], 0, "没有写就不用撤销数据")
        eq(conv.get_conversation_messages(cid), [], "消息仍应撤回")


def check_update_is_reverted():
    with _support.temp_database():
        upsert_asset("现金", 50000)
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid, "t3", "现金改成 48000", write=lambda: upsert_asset("现金", 48000)
        )
        eq(list_assets()["现金"], 48000, "写入后资产应为 48000")

        turn_state.withdraw(cid, "t3")

        eq(list_assets()["现金"], 50000, "撤回后应还原为 50000")


def check_delete_is_restored():
    with _support.temp_database():
        add_transaction("expense", 20, "交通", "2026-10-18T13:00:00")
        tx_id = list_transactions()[0]["id"]
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid, "t4", "把刚才那笔删了", write=lambda: delete_transaction(tx_id)
        )
        eq(list_transactions(), [], "删除后应为空")

        turn_state.withdraw(cid, "t4")

        restored = list_transactions()
        eq(len(restored), 1, "撤回后应恢复一条")
        eq(restored[0]["id"], tx_id, "应恢复原 id（不是新建一条）")


def check_conflict_aborts_whole_withdraw():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid, "t5", "记一笔 30", write=lambda: add_transaction("expense", 30, "餐饮", "2026-10-18T14:00:00")
        )
        tx = list_transactions()[0]

        # 模拟用户在这之后手工改了金额
        conn = get_connection()
        conn.execute("UPDATE transactions SET amount = 99 WHERE id = ?", (tx["id"],))
        conn.commit()
        conn.close()

        exc = raises(turn_state.DataConflict, lambda: turn_state.withdraw(cid, "t5"))
        eq(len(exc.items), 1, "应报告一条冲突")
        true(
            any(t["amount"] == 99 for t in list_transactions()),
            "冲突后不得覆盖用户的修改",
        )
        eq(len(conv.get_conversation_messages(cid)), 2, "冲突时不应撤回消息（整体放弃）")


def check_withdraw_any_turn_truncates_later():
    """撤回中间一轮：这一轮和它之后的全部撤掉，之前的保留。"""
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(
            cid, "t6", "第一轮", write=lambda: add_transaction("expense", 10, "餐饮", "2026-10-18T10:00:00")
        )
        _support.run_turn(
            cid, "t7", "第二轮", write=lambda: add_transaction("expense", 20, "交通", "2026-10-18T11:00:00")
        )
        _support.run_turn(cid, "t8", "第三轮")

        result = turn_state.withdraw(cid, "t7")

        eq(result["turns_withdrawn"], 2, "应撤回 t7 与其后的 t8")
        eq(result["withdrawn_messages"], 4, "两轮各两条消息")
        eq(result["user_text"], "第二轮", "应返回被撤这一轮的用户原话")
        amounts = [t["amount"] for t in list_transactions()]
        eq(amounts, [10], "只应撤销 t7 的写入，t6 的保留")
        contents = [m["content"] for m in conv.get_conversation_messages(cid)]
        eq(contents, ["第一轮", "好的"], "更早的轮次应保留")


def check_withdraw_middle_keeps_earlier():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t9", "甲")
        _support.run_turn(cid, "t10", "乙")
        _support.run_turn(cid, "t11", "丙")

        turn_state.withdraw(cid, "t10")

        contents = [m["content"] for m in conv.get_conversation_messages(cid)]
        eq(contents, ["甲", "好的"], "只保留乙之前的轮次")


def check_repeat_withdraw_is_not_found():
    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t8", "只此一轮")
        turn_state.withdraw(cid, "t8")

        raises(turn_state.TurnNotFound, lambda: turn_state.withdraw(cid, "t8"))


def check_withdrawn_messages_not_replayed():
    """撤回的轮次不能再出现在模型上下文里。"""
    from core.services.replay import expand_to_model_messages

    with _support.temp_database():
        cid = conv.create_conversation()["id"]
        _support.run_turn(cid, "t9", "会被撤回的一轮", assistant_text="回答")
        _support.run_turn(cid, "t10", "保留的一轮", assistant_text="保留的回答")
        turn_state.withdraw(cid, "t10")

        rows = conv.get_conversation_messages(cid)
        replayed = expand_to_model_messages(rows)
        not_in(
            "保留的一轮",
            [m.get("content") for m in replayed],
            "撤回的轮次不应进入上下文",
        )
        in_("会被撤回的一轮", [m.get("content") for m in replayed], "未撤回的轮次仍在")
