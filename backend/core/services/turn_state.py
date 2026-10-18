"""
一轮对话的状态回滚：**对话消息 + 业务数据**。

## 两件事，一个入口

用户点「撤回」时要回到"这条消息没发过"的状态，它包含两层：

1. **对话层**：用户消息与助手消息从会话里消失（`withdrawn=1`），
   下次请求拼上下文时自然看不到；
2. **数据层**：这一轮写进 `transactions / assets / debts / goals / basic_info`
   的行要还原——否则对话说没发生过、账上却多了一笔。

## 为什么用"快照差异"而不是在每个写工具里埋点

埋点要在 9 个 service 函数里各加一段"记 before/after"，漏一个就少撤一条。
这里改成：**轮次开始时给 5 张业务表拍快照，结束时再拍一次，对比出变化的行**，
写进 `turn_writes`。好处：

- 不需要改任何工具/service，**不会漏**；
- 天然覆盖"一轮写了多张表"；
- 差异只存变化的行，体积可控。

代价：每次轮次开始/结束各扫一遍这 5 张表。单用户个人账本（几千行内）可以忽略。

## 撤回的顺序

```
校验（会话存在、这一轮是最后一轮、有写集记录）
  → 逐条核对（当前行 == 记录里的 after 吗？）
  → 有任何冲突就整体放弃（不改一行）
  → 全部还原 + 消息标记撤回 + 重算标题 + 更新 updated_at
```

**整体放弃**是刻意的：宁可撤回失败并告诉用户，也不要撤一半留下更乱的状态。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from core.database import get_connection
from core.services._common import OWNER_ID


class TurnNotFound(LookupError):
    """这一轮不存在（或已撤回）。"""


class NoWritesRecorded(RuntimeError):
    """这一轮还在收尾（快照/差异没生成），暂时不能撤回。"""


class DataConflict(RuntimeError):
    """这一轮写过、但数据在之后被改过；整体放弃撤回。"""

    def __init__(self, items: list):
        super().__init__("数据已被修改，无法安全撤回")
        self.items = items


#: 参与快照的业务表 → 主键列（与 schema 对应）
SNAPSHOT_TABLES: dict[str, tuple[str, ...]] = {
    "transactions": ("id",),
    "assets": ("user_id", "name"),
    "debts": ("user_id", "name"),
    "goals": ("user_id", "goal_id"),
    "basic_info": ("id",),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _pk(table: str, row: dict) -> str:
    """主键的稳定串（dict + sort_keys，便于逐字比较）。"""
    cols = SNAPSHOT_TABLES[table]
    return json.dumps({c: row.get(c) for c in cols}, ensure_ascii=False, sort_keys=True)


def _dump(row: dict | None) -> str | None:
    return None if row is None else json.dumps(row, ensure_ascii=False, sort_keys=True)


def _same(a: dict | None, b: dict | None) -> bool:
    """逐列比较两行（数字统一按 float 比，避免 35 / 35.0 的误判）。"""
    if a is None or b is None:
        return a is b
    if set(a) != set(b):
        return False
    for key, left in a.items():
        right = b[key]
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if float(left) != float(right):
                return False
        elif left != right:
            return False
    return True


# ----------------------------------------------------------------------
# 快照与差异
# ----------------------------------------------------------------------


def capture() -> dict[str, dict[str, dict]]:
    """给 5 张业务表拍快照：{表: {主键串: 行}}。"""
    snapshot: dict[str, dict[str, dict]] = {}
    conn = get_connection()
    try:
        for table in SNAPSHOT_TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            snapshot[table] = {_pk(table, dict(row)): dict(row) for row in rows}
    finally:
        conn.close()
    return snapshot


def diff(before: dict, after: dict) -> list[dict]:
    """对比两次快照，返回变化行列表（insert / update / delete）。"""
    changes: list[dict] = []

    for table in SNAPSHOT_TABLES:
        old_rows = before.get(table, {})
        new_rows = after.get(table, {})
        for key in new_rows.keys() | old_rows.keys():
            old_row = old_rows.get(key)
            new_row = new_rows.get(key)
            if old_row is None and new_row is not None:
                changes.append(
                    {"table": table, "pk": key, "op": "insert",
                     "before": None, "after": new_row}
                )
            elif old_row is not None and new_row is None:
                changes.append(
                    {"table": table, "pk": key, "op": "delete",
                     "before": old_row, "after": None}
                )
            elif not _same(old_row, new_row):
                changes.append(
                    {"table": table, "pk": key, "op": "update",
                     "before": old_row, "after": new_row}
                )

    return changes


def begin(turn_id: str, conversation_id: int) -> dict:
    """轮次开始：登记 turns + 拍快照。返回给 worker 用的 before 快照。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO turns (turn_id, conversation_id, status, created_at)
            VALUES (?, ?, 'running', ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                status = 'running', finished_at = NULL
            """,
            (turn_id, conversation_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return capture()


def finish(turn_id: str, conversation_id: int, before: dict, status: str) -> dict:
    """轮次结束：算差异、落 `turn_writes`、更新 turns 状态。"""
    changes = diff(before, capture())
    now = _now()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM turn_writes WHERE turn_id = ?", (turn_id,))
        for change in changes:
            conn.execute(
                """
                INSERT INTO turn_writes
                    (turn_id, conversation_id, table_name, pk_json, op,
                     before_json, after_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'applied', ?)
                """,
                (
                    turn_id,
                    conversation_id,
                    change["table"],
                    change["pk"],
                    change["op"],
                    _dump(change["before"]),
                    _dump(change["after"]),
                    now,
                ),
            )
        conn.execute(
            "UPDATE turns SET status = ?, finished_at = ? WHERE turn_id = ?",
            (status, now, turn_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"changes": len(changes)}


# ----------------------------------------------------------------------
# 撤回
# ----------------------------------------------------------------------


def _row_exists(conn, table: str, pk: dict) -> dict | None:
    where = " AND ".join(f"{col} = ?" for col in SNAPSHOT_TABLES[table])
    values = [pk[col] for col in SNAPSHOT_TABLES[table]]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {where}", values
    ).fetchone()
    return dict(row) if row is not None else None


def _delete_row(conn, table: str, pk: dict) -> None:
    where = " AND ".join(f"{col} = ?" for col in SNAPSHOT_TABLES[table])
    values = [pk[col] for col in SNAPSHOT_TABLES[table]]
    conn.execute(f"DELETE FROM {table} WHERE {where}", values)


def _restore_row(conn, table: str, row: dict) -> None:
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    usable = [c for c in columns if c in row]
    placeholders = ", ".join(["?"] * len(usable))
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(usable)}) VALUES ({placeholders})",
        [row[c] for c in usable],
    )


def _plan(conn, writes: list[dict]) -> tuple[list[dict], list[dict]]:
    """把每条写翻译成具体动作；返回 (可执行动作, 冲突列表)。"""
    actions: list[dict] = []
    conflicts: list[dict] = []

    for write in reversed(writes):  # 逆序：后做的先撤
        table = write["table_name"]
        pk = json.loads(write["pk_json"])
        before = json.loads(write["before_json"]) if write["before_json"] else None
        after = json.loads(write["after_json"]) if write["after_json"] else None
        current = _row_exists(conn, table, pk)

        if write["op"] == "insert":
            if current is None:
                actions.append({"write": write, "action": "noop"})
            elif not _same(current, after):
                conflicts.append({"write": write, "reason": "行已被修改"})
            else:
                actions.append({"write": write, "action": "delete"})
        elif write["op"] == "delete":
            if current is not None:
                conflicts.append({"write": write, "reason": "行已被重新创建"})
            else:
                actions.append({"write": write, "action": "restore", "row": before})
        else:  # update
            if not _same(current, after):
                conflicts.append({"write": write, "reason": "行已被修改"})
            else:
                actions.append({"write": write, "action": "restore", "row": before})

    return actions, conflicts


def withdraw(conversation_id: int, turn_id: str) -> dict:
    """撤回一轮**及其之后的所有轮次**：消息消失 + 数据还原（全有或全无）。

    语义是"回到这条消息发送之前"：从这一轮起，后面的对话都建立在这轮之上，
    所以一起撤掉，否则上下文会前后矛盾。

    返回摘要：``{turn_id, withdrawn_messages, turns_withdrawn, user_text, rollback}``。
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT status FROM turns WHERE turn_id = ? AND conversation_id = ?",
            (turn_id, conversation_id),
        ).fetchone()
        if row is None or row["status"] == "withdrawn":
            raise TurnNotFound(turn_id)
        if row["status"] == "running":
            # 还在生成：写集没算完，撤回会漏数据
            raise NoWritesRecorded(turn_id)

        rows = conn.execute(
            """
            SELECT id, role, content, turn_id
            FROM messages
            WHERE conversation_id = ? AND withdrawn = 0
            ORDER BY id
            """,
            (conversation_id,),
        ).fetchall()

        start = next(
            (index for index, item in enumerate(rows) if item["turn_id"] == turn_id),
            None,
        )
        if start is None:
            raise TurnNotFound(turn_id)

        affected = rows[start:]
        start_id = affected[0]["id"]
        affected_turns: list[str] = []
        for item in affected:
            if item["turn_id"] and item["turn_id"] not in affected_turns:
                affected_turns.append(item["turn_id"])

        writes: list[dict] = []
        if affected_turns:
            placeholders = ", ".join(["?"] * len(affected_turns))
            writes = [
                dict(item)
                for item in conn.execute(
                    f"""
                    SELECT * FROM turn_writes
                    WHERE conversation_id = ? AND status = 'applied'
                      AND turn_id IN ({placeholders})
                    ORDER BY id
                    """,
                    [conversation_id] + affected_turns,
                ).fetchall()
            ]

        actions, conflicts = _plan(conn, writes)
        if conflicts:
            raise DataConflict(conflicts)

        now = _now()
        for action in actions:
            write = action["write"]
            if action["action"] == "delete":
                _delete_row(conn, write["table_name"], json.loads(write["pk_json"]))
            elif action["action"] == "restore":
                _restore_row(conn, write["table_name"], action["row"])
            conn.execute(
                """
                UPDATE turn_writes
                SET status = 'compensated', compensated_at = ?
                WHERE id = ? AND status = 'applied'
                """,
                (now, write["id"]),
            )

        user_text = next(
            (item["content"] for item in affected if item["role"] == "user"), ""
        )

        # 截断：从这一轮的第一条消息起，全部标记撤回
        conn.execute(
            """
            UPDATE messages
            SET withdrawn = 1, withdrawn_at = ?
            WHERE conversation_id = ? AND id >= ? AND withdrawn = 0
            """,
            (now, conversation_id, start_id),
        )
        if affected_turns:
            placeholders = ", ".join(["?"] * len(affected_turns))
            conn.execute(
                f"UPDATE turns SET status = 'withdrawn', finished_at = ? "
                f"WHERE turn_id IN ({placeholders})",
                [now] + affected_turns,
            )

        # 标题回退：取撤回后第一条用户消息
        first = conn.execute(
            """
            SELECT content FROM messages
            WHERE conversation_id = ? AND role = 'user' AND withdrawn = 0
            ORDER BY id LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        title = (first["content"].strip()[:20] if first else "") or None
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conversation_id),
        )

        conn.commit()
        return {
            "turn_id": turn_id,
            "withdrawn_messages": len(affected),
            "turns_withdrawn": len(affected_turns),
            "user_text": user_text,
            "rollback": {
                "compensated": sum(1 for a in actions if a["action"] != "noop"),
                "noop": sum(1 for a in actions if a["action"] == "noop"),
                "conflicts": 0,
                "failed": 0,
            },
        }
    except (TurnNotFound, NoWritesRecorded, DataConflict):
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
