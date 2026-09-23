"""
数据库连接管理。

职责：定位数据库文件、提供连接工厂 get_connection()。
- 自动创建数据目录与数据库文件；
- 每个连接开启外键约束（PRAGMA foreign_keys = ON）；
- 进程内首次连接执行一次建表检查（懒加载兜底，委托 schema.ensure_schema），
  之后不再重复，显式初始化由 schema.init_db() 负责（FastAPI lifespan 调用）。
"""

import os
import sqlite3

# ---------------------------------------------------------------------------
# 路径：core/database/connection.py -> 向上三级到 backend/ 根目录
# ---------------------------------------------------------------------------
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH: str = os.path.join(BASE_DIR, "infrastructure", "data", "data.db")

# 进程内是否已完成建表检查（仅首次连接执行一次）
_schema_checked: bool = False


def get_connection() -> sqlite3.Connection:
    """获取数据库连接，自动创建目录和数据库文件。

    进程内首次调用时会顺带完成建表检查（表不存在则创建）；
    失败时异常直接向上抛出，由调用方感知。
    """
    global _schema_checked

    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if not _schema_checked:
        # 延迟导入：schema 模块顶部依赖本模块的 get_connection，避免循环导入
        from core.database.schema import ensure_schema

        ensure_schema(conn)
        _schema_checked = True

    return conn
