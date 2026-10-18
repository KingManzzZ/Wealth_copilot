"""被中止轮次的落库与回放语义。

产品决策：**被中止的 assistant 轮次整轮不进模型上下文**（半截回答会让模型以为
自己说完了，工具也可能只执行了一部分），但**用户那条消息保留**——否则模型会
以为用户没说过，下一轮容易重复追问。

另外覆盖旧库迁移：``messages`` 表补 ``interrupted`` / ``stop_reason`` 两列。
"""

import sqlite3

import _support
from run_tests import eq, in_, not_in, true

from core.agent.context import prepare_history
from core.database.schema import ensure_schema
from core.services import conversations as conv
from core.services.replay import expand_to_model_messages


def _append_interrupted(conversation_id: int) -> None:
    conv.append_messages(
        conversation_id,
        [
            {
                "role": "assistant",
                "content": "半截回答",
                "interrupted": True,
                "stop_reason": "client",
                "tool_calls": [
                    {
                        "id": "call_x",
                        "name": "search_knowledge",
                        "arguments": {"query": "个人养老金"},
                        "status": "running",
                        "startedAt": 0,
                    }
                ],
            }
        ],
    )


def check_interrupted_flags_are_persisted():
    with _support.temp_database():
        conversation_id = conv.create_conversation()["id"]
        conv.append_messages(conversation_id, [{"role": "user", "content": "问题"}])
        _append_interrupted(conversation_id)

        rows = conv.get_conversation_messages(conversation_id)
        eq(len(rows), 2, "两行都应落库（被中止的也要能展示）")
        assistant = rows[1]
        eq(assistant["interrupted"], True, "interrupted 应落库为 True")
        eq(assistant["stop_reason"], "client", "stop_reason 应落库")
        eq(assistant["content"], "半截回答", "已生成的内容应保留")

        normal = conv.append_messages(
            conversation_id, [{"role": "assistant", "content": "正常回答"}]
        )
        true(normal is not None, "正常写入应成功")


def check_replay_skips_interrupted_but_keeps_user():
    with _support.temp_database():
        conversation_id = conv.create_conversation()["id"]
        conv.append_messages(conversation_id, [{"role": "user", "content": "问题一"}])
        _append_interrupted(conversation_id)
        conv.append_messages(conversation_id, [{"role": "user", "content": "问题二"}])
        conv.append_messages(conversation_id, [{"role": "assistant", "content": "正常回答"}])

        rows = conv.get_conversation_messages(conversation_id)
        replayed = expand_to_model_messages(rows)

        eq(
            [m["role"] for m in replayed],
            ["user", "user", "assistant"],
            "被中止的 assistant 应被跳过，两条 user 保留",
        )
        eq(replayed[0]["content"], "问题一", "用户消息内容应保留")
        not_in("半截回答", [m.get("content") for m in replayed], "半截回答不应进入上下文")

        # 被中止轮次里的 tool_call 是嵌在同一个 assistant 行里的，跳过整行即整轮消失
        true(
            not any(m.get("role") == "tool" for m in replayed),
            "被中止轮次的 tool 消息也不应出现（否则会与缺失的 tool_calls 不配对）",
        )


def check_prepare_history_excludes_interrupted():
    with _support.temp_database():
        conversation_id = conv.create_conversation()["id"]
        conv.append_messages(conversation_id, [{"role": "user", "content": "问题"}])
        _append_interrupted(conversation_id)
        conv.append_messages(conversation_id, [{"role": "assistant", "content": "正常回答"}])

        rows = conv.get_conversation_messages(conversation_id)
        history, info = prepare_history(rows)

        eq(len(history), 2, "上下文只应有 user + 正常 assistant")
        not_in(
            "半截回答",
            [m.get("content") or "" for m in history],
            "被中止的轮次不应进入上下文",
        )
        eq(info["rows"], 3, "度量应报告原始行数（裁剪是下游的事）")


def check_rows_without_flag_replay_normally():
    """旧数据没有 interrupted 字段：按正常轮次处理，不能误删历史。"""
    rows = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    replayed = expand_to_model_messages(rows)
    eq([m["role"] for m in replayed], ["user", "assistant"], "旧行应正常回放")
    eq(replayed[1]["content"], "b", "旧行内容应保留")


def check_migration_adds_columns():
    db_path = _support.test_tmp_dir() / "legacy-messages.db"
    try:
        db_path.unlink()
    except OSError:
        pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            tool_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', '旧数据');
        """
    )
    conn.commit()

    ensure_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    in_("interrupted", columns, "应补上 interrupted 列")
    in_("stop_reason", columns, "应补上 stop_reason 列")

    row = conn.execute(
        "SELECT interrupted, stop_reason FROM messages WHERE content = '旧数据'"
    ).fetchone()
    eq(row, (0, None), "旧行默认应为 0/NULL（等价于「历史上没被中断」）")
    conn.close()


def check_migration_is_idempotent():
    db_path = _support.test_tmp_dir() / "legacy-messages-2.db"
    try:
        db_path.unlink()
    except OSError:
        pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            tool_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()

    ensure_schema(conn)
    ensure_schema(conn)  # 第二次不应报错（ADD COLUMN 只在缺失时执行）
    columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    eq(columns.count("interrupted"), 1, "重复迁移不应重复加列")
    conn.close()
