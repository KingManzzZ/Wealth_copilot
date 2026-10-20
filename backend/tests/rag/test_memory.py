"""L3 跨会话记忆：存储约束、检索过滤、注入渲染、工具契约。"""

from __future__ import annotations

from datetime import date, timedelta

import _support
from run_tests import eq, in_, raises, true

from core.agent import agent, l3
from core.services import memories
from core.tools import tools_route, tools_schema
from core.tools.tools_hub import memory_tools


def check_add_and_list():
    with _support.temp_database():
        note = memories.add_note("preference", "偏好稳健型产品")
        true(note["id"] > 0, "应返回 id")
        eq(note["category"], "preference")

        notes = memories.list_for_prompt()
        eq(len(notes), 1)
        eq(notes[0]["content"], "偏好稳健型产品")
        eq(notes[0]["source"], "user_stated")


def check_category_whitelist():
    with _support.temp_database():
        raises(ValueError, lambda: memories.add_note("hobby", "喜欢钓鱼"))
        raises(ValueError, lambda: memories.add_note("preference", ""))


def check_duplicate_is_idempotent():
    with _support.temp_database():
        first = memories.add_note("constraint", "不接受股票类产品")
        second = memories.add_note("constraint", "不接受股票类产品")
        eq(second["id"], first["id"], "重复内容应返回同一条")
        eq(second.get("duplicate"), True)
        eq(len(memories.list_notes()), 1)


def check_inferred_not_in_prompt():
    """模型推测的条目可以存，但绝不能进 prompt。"""
    with _support.temp_database():
        memories.add_note("fact", "用户可能讨厌长期锁定期", source="model_inferred")
        eq(memories.list_for_prompt(), [], "inferred 不应进 prompt")
        eq(len(memories.list_notes(include_inferred=True)), 1)


def check_expired_not_in_prompt():
    with _support.temp_database():
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        memories.add_note("preference", "这个月想省点钱", expires_at=yesterday)
        eq(memories.list_for_prompt(), [], "过期条目不应进 prompt")
        eq(len(memories.list_notes(include_expired=True)), 1)


def check_forget_is_soft_delete():
    with _support.temp_database():
        note = memories.add_note("fact", "每月 10 号发工资")
        true(memories.forget_note(note["id"]))
        eq(memories.list_for_prompt(), [])
        eq(memories.forget_note(note["id"]), False, "已删除的不能再删")


def check_render_notes():
    notes = [
        {
            "id": 3,
            "category": "constraint",
            "content": "不接受股票类产品",
            "updated_at": "2026-10-01T10:00:00",
        },
        {
            "id": 4,
            "category": "preference",
            "content": "偏好稳健型",
            "updated_at": "2026-10-02T10:00:00",
        },
    ]
    text = l3.render_notes(notes)
    in_("长期记忆", text, "应有明确标题")
    in_("#3", text, "应带 id，供 forget_note 使用")
    in_("[约束]", text, "应标注类别")
    in_("2026-10", text, "应带时间，便于判断时效")
    in_("不是指令", text, "应说明记忆是数据不是指令")
    eq(l3.render_notes([]), "", "无记忆时不应注入空块")


def check_render_respects_cap():
    notes = [
        {"id": i, "category": "fact", "content": "很长的记忆内容" * 20, "updated_at": "2026-10-01"}
        for i in range(1, 21)
    ]
    text = l3.render_notes(notes, max_chars=300)
    true(len(text) <= 340, f"应受长度上限约束（实际 {len(text)}）")
    true(text.count("\n") < 10, "不应把 20 条全塞进去")


def check_user_content_includes_notes():
    notes = [
        {
            "id": 3,
            "category": "constraint",
            "content": "不接受股票类产品",
            "updated_at": "2026-10-01T10:00:00",
        }
    ]
    text = agent._user_content("推荐个方案", notes)
    in_("当前日期", text)
    in_("长期记忆", text)
    in_("#3", text)
    true(text.endswith("推荐个方案"), "用户原话应在最后")

    plain = agent._user_content("推荐个方案")
    true("长期记忆" not in plain, "没有记忆时不应出现记忆块")


def check_tools_registered():
    names = [item["function"]["name"] for item in tools_schema.TOOLS_SCHEMA]
    in_("remember_note", names)
    in_("forget_note", names)
    in_("remember_note", tools_route.TOOL_REGISTRY)
    in_("forget_note", tools_route.TOOL_REGISTRY)


def check_remember_tool_roundtrip():
    with _support.temp_database():
        saved = memory_tools.remember_note("preference", "偏好稳健型产品")
        eq(saved["ok"], True)
        true(saved["id"] > 0)
        eq(len(memories.list_for_prompt()), 1)

        forgotten = memory_tools.forget_note(saved["id"])
        eq(forgotten["ok"], True)
        eq(memories.list_for_prompt(), [])


def check_remember_tool_rejects_bad_category():
    with _support.temp_database():
        result = memory_tools.remember_note("hobby", "喜欢钓鱼")
        eq(result.get("error_kind"), "invalid_note")
        true("error" in result)
