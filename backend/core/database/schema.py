"""
数据库表结构定义与初始化。

职责：维护全部建表 DDL，提供幂等的初始化入口。
单用户模式，不创建 users 表；通过 OWNER_ID 常量统一管理用户归属，
未来扩展多用户时只需为各表添加 user_id 字段。
"""

import sqlite3

from core.database.connection import get_connection

# 单用户归属标识，未来扩展多用户时替换为真实的 user_id
OWNER_ID: int = 1

# 建表脚本：全部 IF NOT EXISTS，可重复执行（幂等）
SCHEMA_SQL: str = """
        -- ===================================================================
        -- 2.1 financial_profiles：财务画像表
        -- 完整画像以 JSON 存于 profile_data（字段契约见 backend/API.md）；
        -- monthly_income、risk_tolerance 为冗余列，便于直接做 SQL 统计；
        -- 单用户模式只维护一条记录（id 固定为 OWNER_ID）。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS financial_profiles (
            id              INTEGER PRIMARY KEY,
            monthly_income  NUMERIC NOT NULL DEFAULT 0,
            risk_tolerance  TEXT    NOT NULL DEFAULT '',
            profile_data    TEXT    NOT NULL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
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
        """


def ensure_schema(conn: sqlite3.Connection) -> None:
    """在给定连接上执行建表脚本（幂等；失败异常向上抛出）。"""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def init_db() -> None:
    """初始化数据库：创建所有表（幂等）。
    """
    conn = get_connection()
   
