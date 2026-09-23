"""
基本信息 Service 层。

职责：管理 basic_info 表（单用户单行记录，id = user_id = OWNER_ID）。
原 financial_profiles 表的 profile_data JSON 设计已废弃；
monthly_income/monthly_expense 不再存为字段，由 cashflow service 派生。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now, _sanitize, _is_filled

# 基本信息表可写字段（用户可编辑；与前端 BasicInfo 契约对齐）
# risk_tolerance / investment_preference / liquidity_needs 为算法估算字段，不可用户修改
BASIC_INFO_FIELDS: tuple[str, ...] = (
    "name",
    "age",
    "city",
    "occupation",
    "industry",
    "education",
    "family",
    "marital_status",
    "children",
    "supporting_parents",
    "investing_years",
)

# 资料完善度清单（含算法字段，共 12 项）
COMPLETION_FIELDS: tuple[str, ...] = (
    "name",
    "age",
    "city",
    "occupation",
    "industry",
    "education",
    "risk_tolerance",
    "investment_preference",
    "investing_years",
    "liquidity_needs",
    "marital_status",
    "children",
    "supporting_parents",
)


def _default_basic_info() -> dict:
    """画像不存在时的默认值。"""
    return {
        "user_id": "default",
        "name": "",
        "age": 0,
        "city": "",
        "occupation": "",
        "industry": "",
        "education": "",
        "family": "",
        "marital_status": "",
        "children": "",
        "supporting_parents": "",
        "risk_tolerance": "",
        "investment_preference": "",
        "investing_years": 0,
        "liquidity_needs": "",
        "last_updated": "",
    }


def _row_to_dict(row) -> dict:
    """将 basic_info 表行转为前端契约 dict（updated_at 映射为 last_updated）。"""
    return {
        "user_id": f"user-{row['user_id']}",
        "name": row["name"],
        "age": row["age"],
        "city": row["city"],
        "occupation": row["occupation"],
        "industry": row["industry"],
        "education": row["education"],
        "family": row["family"],
        "marital_status": row["marital_status"],
        "children": row["children"],
        "supporting_parents": row["supporting_parents"],
        "risk_tolerance": row["risk_tolerance"],
        "investment_preference": row["investment_preference"],
        "investing_years": row["investing_years"],
        "liquidity_needs": row["liquidity_needs"],
        "last_updated": row["updated_at"],
    }


def get_basic_info() -> dict:
    """获取当前用户基本信息；尚未创建时返回默认值。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM basic_info WHERE id = ?",
            (OWNER_ID,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row is not None else _default_basic_info()


def upsert_basic_info(data: dict) -> dict:
    """UPSERT 基本信息：仅接受白名单字段，整体覆盖。

    单用户模式下固定 id = user_id = OWNER_ID = 1。
    """
    cleaned = _sanitize(data, BASIC_INFO_FIELDS)
    # 动态构造列与占位符，避免硬编码列顺序
    columns = list(cleaned.keys())
    # ON CONFLICT DO UPDATE 引用 excluded.<col>，无需额外绑定参数
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in columns)
    values = [cleaned[c] for c in columns]

    conn = get_connection()
    try:
        if columns:
            conn.execute(
                f"""
                INSERT INTO basic_info (id, user_id, {', '.join(columns)}, updated_at)
                VALUES (?, ?, {', '.join(['?'] * len(columns))}, ?)
                ON CONFLICT(id) DO UPDATE SET
                    {set_clause},
                    updated_at = excluded.updated_at
                """,
                [OWNER_ID, OWNER_ID] + values + [_now()],
            )
            conn.commit()
        row = conn.execute(
            "SELECT * FROM basic_info WHERE id = ?",
            (OWNER_ID,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row is not None else _default_basic_info()


def get_profile_completion() -> float:
    """资料完善度：完善度清单已填写占比（0.0 ~ 1.0）。"""
    info = get_basic_info()
    filled = sum(1 for field in COMPLETION_FIELDS if _is_filled(info.get(field)))
    return round(filled / len(COMPLETION_FIELDS), 4)


def is_onboarding_completed() -> bool:
    """判断首次设置是否完成：必填字段全部有效。

    必填字段已不再包含 monthly_income/monthly_expense（已从 cashflow 派生）。
    """
    info = get_basic_info()
    required = ("name", "age", "occupation")
    return all(_is_filled(info.get(field)) for field in required)
