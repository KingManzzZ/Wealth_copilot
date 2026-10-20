"""
数据库表结构定义与初始化。

职责：维护全部建表 DDL，提供幂等的初始化入口。
单用户模式：所有表通过 user_id 列预埋多用户扩展（当前固定为 OWNER_ID = 1）。
原 financial_profiles 单表 JSON 设计已废弃，按关系建模拆为 6 张独立表。
"""

import sqlite3

from core.database.connection import get_connection

# 单用户归属标识，未来扩展多用户时替换为真实的 user_id
OWNER_ID: int = 1

# 建表脚本：全部 IF NOT EXISTS，可重复执行（幂等）
SCHEMA_SQL: str = """
        -- ===================================================================
        -- 1. basic_info：用户基本信息表（原 financial_profiles 拆分）
        -- 单用户模式下只维护一行（id = user_id = OWNER_ID = 1）。
        -- 不再含 monthly_income/monthly_expense（从 transactions 按月派生）。
        -- 不再含 profile_data JSON 列；所有字段都是原子列。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS basic_info (
            id                   INTEGER PRIMARY KEY,
            user_id              INTEGER NOT NULL DEFAULT 1,
            name                 TEXT    NOT NULL DEFAULT '',
            age                  NUMERIC NOT NULL DEFAULT 0,
            city                 TEXT    NOT NULL DEFAULT '',
            occupation           TEXT    NOT NULL DEFAULT '',
            industry             TEXT    NOT NULL DEFAULT '',
            education            TEXT    NOT NULL DEFAULT '',
            family               TEXT    NOT NULL DEFAULT '',
            marital_status       TEXT    NOT NULL DEFAULT '',
            children             TEXT    NOT NULL DEFAULT '',
            supporting_parents   TEXT    NOT NULL DEFAULT '',
            risk_tolerance       TEXT    NOT NULL DEFAULT '',
            investment_preference TEXT   NOT NULL DEFAULT '',
            investing_years      NUMERIC NOT NULL DEFAULT 0,
            liquidity_needs      TEXT    NOT NULL DEFAULT '',
            created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 2. assets：资产表
        -- 对齐前端 Record<string, number>：name 为前端字典 key，value 为估值。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS assets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL DEFAULT 1,
            name        TEXT    NOT NULL,
            value       NUMERIC NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        );

        CREATE INDEX IF NOT EXISTS ix_assets_user ON assets (user_id);

        -- ===================================================================
        -- 3. debts：债务表（原 liabilities 改名，对齐前端命名）
        -- 对齐前端 Record<string, { remaining, monthly_payment }>。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS debts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL DEFAULT 1,
            name             TEXT    NOT NULL,
            remaining        NUMERIC NOT NULL DEFAULT 0,
            monthly_payment  NUMERIC NOT NULL DEFAULT 0,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        );

        CREATE INDEX IF NOT EXISTS ix_debts_user ON debts (user_id);

        -- ===================================================================
        -- 4. goals：财富目标表
        -- 对齐前端 FinancialGoal[]：goal_id 为前端用的字符串 id（如 "g1"）。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    goal_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    target      NUMERIC NOT NULL,
    current     NUMERIC NOT NULL DEFAULT 0,
    deadline    TEXT,
    priority    TEXT    NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('high', 'medium', 'low')),
    status      TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed', 'paused')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, goal_id)
);

        CREATE INDEX IF NOT EXISTS ix_goals_user ON goals (user_id);

        -- ===================================================================
        -- 5. transactions：单月收支详情表（交易流水）
        -- 复用原 schema 字段并新增 user_id 列。
        --
        -- **这是收支的唯一数据源**：月度收入 / 支出 / 结余全部由它按
        -- ``strftime('%Y-%m', occurred_at)`` 聚合派生（见 services/cashflow.py）。
        -- 原来并存的 monthly_cashflow 汇总表已删除——同一事实存两份且无同步，
        -- 会让"记了流水"与"月结余不变"同时成立（BK-27 / G-11）。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS transactions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL DEFAULT 1,
            transaction_type  TEXT    NOT NULL CHECK (transaction_type IN ('income', 'expense')),
            amount            NUMERIC NOT NULL CHECK (amount > 0),
            category          TEXT    NOT NULL,
            source            TEXT,
            occurred_at       TEXT    NOT NULL,
            note              TEXT,
            created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_transactions_user_occurred
            ON transactions (user_id, occurred_at);

        CREATE INDEX IF NOT EXISTS ix_transactions_user_type_category
            ON transactions (user_id, transaction_type, category);

        -- ===================================================================
        -- 6. conversations：对话表
        -- 保存 Agent 对话会话及摘要。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL DEFAULT 1,
            title       TEXT,
            summary     TEXT,
            -- 摘要覆盖到哪条消息（含）；<= 它的消息不再逐条进入上下文
            summary_until_message_id INTEGER,
            -- 锚点：会话第一条用户消息，永不参与摘要（防"忘了最初要干嘛"）
            summary_anchor_message_id INTEGER,
            status      TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'archived')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ===================================================================
        -- 7. messages：消息表
        -- 保存用户、Agent 和工具之间的消息。
        -- 删除对话时级联删除关联消息。
        --
        -- turn_id：一轮对话的 id（用户消息 + 助手消息共用），撤回时按它定位。
        -- withdrawn：被用户撤回。撤回后**不进上下文、不出现在消息列表**，
        -- 但行仍保留（审计 + 数据回滚的依据）。
        -- interrupted / stop_reason：本轮生成被用户中止或连接断开时置位。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS messages (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id   INTEGER NOT NULL
                                REFERENCES conversations (id) ON DELETE CASCADE,
            role              TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content           TEXT    NOT NULL,
            tool_calls        TEXT,
            tool_name         TEXT,
            turn_id           TEXT,
            interrupted       INTEGER NOT NULL DEFAULT 0,
            stop_reason       TEXT,
            withdrawn         INTEGER NOT NULL DEFAULT 0,
            withdrawn_at      TEXT,
            created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- ix_messages_turn 不能放在这里：旧库的 messages 表还没有 turn_id，
        -- CREATE INDEX 会在补列之前失败。它由 _ensure_message_columns 在补列后创建。

        -- ===================================================================
        -- 8. turns：一轮生成的生命周期（撤回时判断"是否最后一轮"用）
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS turns (
            turn_id         TEXT PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            status          TEXT    NOT NULL,   -- running | completed | interrupted | withdrawn
            created_at      TEXT    NOT NULL,
            finished_at     TEXT
        );

        -- ===================================================================
        -- 9. turn_writes：一轮写入的**差异日志**（撤回时按它还原）
        --
        -- 只在轮次结束时生成：对比"轮前快照"与"轮后快照"，把变化的行记下来。
        -- 这样不需要在每个写工具里埋点，也不会漏掉任何一条写。
        --
        -- op: insert（before 为空）/ delete（after 为空）/ update
        -- status: applied（待撤回）| compensated（已撤回）| conflict（数据被改过，跳过）
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS turn_writes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            turn_id         TEXT    NOT NULL,
            conversation_id INTEGER NOT NULL,
            table_name      TEXT    NOT NULL,
            pk_json         TEXT    NOT NULL,
            op              TEXT    NOT NULL,
            before_json     TEXT,
            after_json      TEXT,
            status          TEXT    NOT NULL DEFAULT 'applied',
            detail          TEXT,
            created_at      TEXT    NOT NULL,
            compensated_at  TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_turn_writes_turn
            ON turn_writes (turn_id, status);

        -- ===================================================================
        -- 10. compactions：每次上下文压缩的审计记录
        --
        -- 为什么要单独记：摘要是有损且不确定的操作。线上"怎么突然变笨了"，
        -- 必须能回答"第几轮压的、压了多少、前后是什么、用的哪版提示词"。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS compactions (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id          INTEGER NOT NULL,
            covered_from_message_id  INTEGER NOT NULL,
            covered_until_message_id INTEGER NOT NULL,
            before_tokens            INTEGER NOT NULL,
            after_tokens             INTEGER NOT NULL,
            summary                  TEXT    NOT NULL,
            prompt_version           TEXT    NOT NULL,
            created_at               TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_messages_conversation_created
            ON messages (conversation_id, created_at);

        -- ===================================================================
        -- 11. user_notes：跨会话的"非结构化偏好/约束/事实"（L3）
        --
        -- 为什么只补这一小块：财务事实（资产/负债/目标/流水）已经是结构化表，
        -- 抽取只会引入误差。真正缺的是"不接受股票""偏好稳健"这类表里放不下的东西。
        --
        -- category 白名单；source=model_inferred 的条目**不进 prompt**
        -- （防止一次模型推测变成永久偏见）；expires_at 支持时效。
        -- ===================================================================
        CREATE TABLE IF NOT EXISTS user_notes (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                 INTEGER NOT NULL DEFAULT 1,
            category                TEXT    NOT NULL
                                    CHECK (category IN ('preference', 'constraint', 'fact')),
            content                 TEXT    NOT NULL,
            source                  TEXT    NOT NULL DEFAULT 'user_stated'
                                    CHECK (source IN ('user_stated', 'model_inferred')),
            source_conversation_id  INTEGER,
            status                  TEXT    NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'superseded', 'deleted')),
            expires_at              TEXT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_user_notes_active
            ON user_notes (user_id, status, category);
        """


def ensure_schema(conn: sqlite3.Connection) -> None:
    """在给定连接上执行建表脚本（幂等；失败异常向上抛出），并做轻量迁移。"""
    conn.executescript(SCHEMA_SQL)
    _ensure_message_columns(conn)
    _ensure_conversation_columns(conn)
    _drop_legacy_cashflow(conn)
    conn.commit()


def _ensure_conversation_columns(conn: sqlite3.Connection) -> None:
    """给旧库的 conversations 补上压缩相关列（幂等）。"""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    except sqlite3.OperationalError:
        return

    additions = {
        "summary_until_message_id": "INTEGER",
        "summary_anchor_message_id": "INTEGER",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE conversations ADD COLUMN {column} {ddl}")
            print(f"[schema] conversations 表已补列：{column}")


def _ensure_message_columns(conn: sqlite3.Connection) -> None:
    """给旧库补齐 messages 的新列（幂等）。

    用 ``ALTER TABLE ADD COLUMN`` 而不是重建表：SQLite 的 ADD COLUMN 只改元数据、
    不重写行；旧行默认 0/NULL，语义正好是"历史上没有被打断/撤回的轮次"。

    为什么不靠建表脚本：``CREATE TABLE IF NOT EXISTS`` 对已存在的表是空操作，
    **不会补列**（SQLite 的既定行为），所以必须有这一步。
    """
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    except sqlite3.OperationalError:
        return  # 连 messages 表都还没有，交给建表脚本

    additions = {
        "turn_id": "TEXT",
        "interrupted": "INTEGER NOT NULL DEFAULT 0",
        "stop_reason": "TEXT",
        "withdrawn": "INTEGER NOT NULL DEFAULT 0",
        "withdrawn_at": "TEXT",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {ddl}")
            print(f"[schema] messages 表已补列：{column}")

    # 索引必须等 turn_id 补完再建，否则旧库会在 executescript 阶段就失败
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_turn ON messages (turn_id)"
    )


def _drop_legacy_cashflow(conn: sqlite3.Connection) -> None:
    """删除 v0.5 之前的 ``monthly_cashflow`` 汇总表。

    月度收支已改为由 ``transactions`` 聚合派生，这张表不再被任何代码读写；
    留着它等于给"手写汇总"留了一个后门，会重新制造"两份收支"的问题。

    **只在表为空时删除**：非空说明库里有历史手工汇总，宁可保留并告警让人处理，
    也不能静默丢数据（它是旧版本里唯一记录过汇总数据的地方）。
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM monthly_cashflow").fetchone()
    except sqlite3.OperationalError:
        return  # 表不存在（新库），无需处理

    count = row[0] if row else 0
    if count == 0:
        conn.execute("DROP TABLE monthly_cashflow")
        print("[schema] 已删除遗留的 monthly_cashflow 表（改为由 transactions 派生）")
        return

    print(
        f"[schema] ⚠️ 遗留 monthly_cashflow 表含 {count} 行手工汇总，已停止使用但未删除。"
        "如需保留这些数字，请转成 transactions 流水后手工删表。"
    )


def init_db() -> None:
    """初始化数据库：创建所有表（幂等）。

    供应用启动时显式调用（FastAPI lifespan）；不调用也有兜底——
    get_connection() 进程内首次连接会自动执行一次同样的建表检查。
    """
    conn = get_connection()
    try:
        ensure_schema(conn)
    finally:
        conn.close()
