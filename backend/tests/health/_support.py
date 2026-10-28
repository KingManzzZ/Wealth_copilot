"""财务健康度测试共用：一次性数据库 + 造数工具。"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parents[1]

for path in (str(BACKEND_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


@contextmanager
def temp_database():
    """一次性数据库（不碰 backend/db/data.db）。"""
    import core.database.connection as connection

    base = os.environ.get("WP_TEST_TMP")
    root = Path(base) if base else BACKEND_DIR.parent / ".tmp" / "health-tests"
    root.mkdir(parents=True, exist_ok=True)
    db_dir = root / f"db-{uuid.uuid4().hex}"
    db_dir.mkdir(parents=True, exist_ok=True)

    prev_path, prev_checked = connection.DB_PATH, connection._schema_checked
    connection.DB_PATH = str(db_dir / "data.db")
    connection._schema_checked = False
    try:
        yield
    finally:
        connection.DB_PATH, connection._schema_checked = prev_path, prev_checked
        shutil.rmtree(db_dir, ignore_errors=True)


def set_risk_tolerance(level: str) -> None:
    """写入一条 basic_info 并设置风险承受能力。"""
    from core.database import get_connection
    from core.services.basic_info import upsert_basic_info

    upsert_basic_info({"name": "测试用户", "age": 30, "occupation": "工程师"})
    conn = get_connection()
    try:
        conn.execute("UPDATE basic_info SET risk_tolerance = ? WHERE id = 1", (level,))
        conn.commit()
    finally:
        conn.close()


def add_month(income: float, expense: float, months_ago: int = 0) -> None:
    """在指定月份写一笔收入 + 一笔支出。"""
    from core.services.transactions import add_transaction

    day = date.today().replace(day=15) - timedelta(days=30 * months_ago)
    stamp = f"{day.year:04d}-{day.month:02d}-15T12:00:00"
    if income:
        add_transaction("income", income, "薪资", stamp)
    if expense:
        add_transaction("expense", expense, "生活", stamp)


def add_goal(
    goal_id: str,
    target: float,
    current: float,
    deadline: date,
    created_at: date | None = None,
) -> None:
    """创建目标，并（可选）把 created_at 改到过去。"""
    from core.database import get_connection
    from core.services.goals import upsert_goal

    upsert_goal(
        {
            "id": goal_id,
            "name": goal_id,
            "target": target,
            "current": current,
            "deadline": deadline.isoformat(),
            "priority": "medium",
        }
    )
    if created_at is not None:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE goals SET created_at = ? WHERE goal_id = ?",
                (f"{created_at.isoformat()} 00:00:00", goal_id),
            )
            conn.commit()
        finally:
            conn.close()
