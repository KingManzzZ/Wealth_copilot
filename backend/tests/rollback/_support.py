"""撤回测试共用：一次性数据库 + 造一轮的工具。"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parents[1]

for path in (str(BACKEND_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def test_tmp_dir() -> Path:
    override = os.environ.get("WP_TEST_TMP")
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(tempfile.mkdtemp(prefix="wp-rollback-test-"))


@contextmanager
def temp_database():
    """把数据库连接指向一次性文件；退出时恢复并清理。"""
    import core.database.connection as connection

    db_path = test_tmp_dir() / f"data-{uuid.uuid4().hex}.db"
    previous_path = connection.DB_PATH
    previous_checked = connection._schema_checked
    connection.DB_PATH = str(db_path)
    connection._schema_checked = False
    try:
        yield db_path
    finally:
        connection.DB_PATH = previous_path
        connection._schema_checked = previous_checked
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(f"{db_path}{suffix}")
            except OSError:
                pass


def run_turn(
    conversation_id: int,
    turn_id: str,
    user_text: str,
    write=None,
    assistant_text: str = "好的",
) -> dict:
    """模拟一轮：轮前快照 → 执行写 → 轮后差异 → 落两条消息。"""
    from core.services import conversations as conv
    from core.services import turn_state

    before = turn_state.begin(turn_id, conversation_id)
    if write is not None:
        write()
    result = turn_state.finish(turn_id, conversation_id, before, "completed")
    conv.append_messages(
        conversation_id, [{"role": "user", "content": user_text, "turn_id": turn_id}]
    )
    conv.append_messages(
        conversation_id,
        [{"role": "assistant", "content": assistant_text, "turn_id": turn_id}],
    )
    return result
