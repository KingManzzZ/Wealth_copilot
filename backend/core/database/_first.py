"""
WealthPilot V1 数据库初始化（原生 SQLite 版本）。

单用户模式，不创建 users 表。
通过 OWNER_ID 常量统一管理用户归属，未来扩展多用户时只需为各表添加 user_id 字段。
"""

import os
import sqlite3

# ---------------------------------------------------------------------------
# 单用户归属标识，未来扩展多用户时替换为真实的 user_id
# ---------------------------------------------------------------------------
OWNER_ID: int = 1

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH: str = os.path.join(BASE_DIR, "infrastructure", "data", "data.db")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接，自动创建目录和数据库文件。"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """创建所有数据表，若已存在则跳过。"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.executescript("""
        -- ===================================================================
        -- 2.1 financial_profiles：财务画像表
        -- 保存用户相对稳定的财务画像信息，单用户模式下只维护一条记录。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS financial_profiles (
            id              INTEGER PRIMARY KEY,
            monthly_income  NUMERIC,
            income_stability TEXT,
            risk_preference TEXT,
            profile_data    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.2 transactions：收支流水表
        -- 统一记录收入和支出。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS transactions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_type  TEXT    NOT NULL CHECK (transaction_type IN ('income', 'expense')),
            amount            NUMERIC NOT NULL CHECK (amount > 0),
            category          TEXT    NOT NULL,
            source            TEXT,
            occurred_at       TEXT    NOT NULL,
            note              TEXT,
            created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_transactions_occurred_at
            ON transactions (occurred_at);

        CREATE INDEX IF NOT EXISTS ix_transactions_type_category
            ON transactions (transaction_type, category);

        -- ===================================================================
        -- 2.3 assets：资产表
        -- 记录用户当前持有的资产及估值。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS assets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            asset_type      TEXT    NOT NULL,
            current_value   NUMERIC NOT NULL CHECK (current_value >= 0),
            currency        TEXT    NOT NULL DEFAULT 'CNY',
            valuation_date  TEXT,
            note            TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.4 liabilities：负债表
        -- 记录用户当前负债情况。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS liabilities (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT    NOT NULL,
            liability_type        TEXT    NOT NULL,
            current_balance       NUMERIC NOT NULL CHECK (current_balance >= 0),
            annual_interest_rate  NUMERIC CHECK (annual_interest_rate >= 0),
            minimum_payment       NUMERIC CHECK (minimum_payment >= 0),
            due_date              TEXT,
            note                  TEXT,
            created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.5 goals：财富目标表
        -- 保存用户的储蓄、购置和其他财富目标。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS goals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            target_amount   NUMERIC NOT NULL CHECK (target_amount > 0),
            current_amount  NUMERIC NOT NULL DEFAULT 0 CHECK (current_amount >= 0),
            deadline        TEXT,
            priority        INTEGER NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'completed', 'paused')),
            note            TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.6 wealth_plans：财富计划表
        -- 保存 Agent 生成的财富计划。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS wealth_plans (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT    NOT NULL,
            plan_content          TEXT    NOT NULL,
            monthly_saving_target NUMERIC,
            status                TEXT    NOT NULL DEFAULT 'draft'
                                            CHECK (status IN ('draft', 'active', 'archived')),
            effective_from        TEXT,
            effective_to          TEXT,
            created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.7 conversations：对话表
        -- 保存 Agent 对话会话及摘要。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            summary     TEXT,
            status      TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'archived')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2.8 messages：消息表
        -- 保存用户、Agent 和工具之间的消息。
        -- 删除对话时级联删除关联消息。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS messages (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id   INTEGER NOT NULL
                                REFERENCES conversations (id) ON DELETE CASCADE,
            role              TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content           TEXT    NOT NULL,
            tool_calls        TEXT,
            tool_name         TEXT,
            created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_messages_conversation_created
            ON messages (conversation_id, created_at);
        """)
        conn.commit()
    except sqlite3.Error as e:
        print(f"初始化数据库失败: {e}")
        conn.rollback()
    finally:
        conn.close()
