"""
财务画像 Service 层。

职责：财务画像的业务逻辑（读取、创建、更新、完善度计算、首次设置判断）。
单用户模式下 financial_profiles 表只维护一条记录（id 固定为 OWNER_ID），
完整画像以 JSON 形式存储于 profile_data 字段；monthly_income 与
risk_tolerance 同步冗余到独立列，便于将来直接做 SQL 统计。

连接策略：每个函数自建连接、用完即关，兼容 HTTP 路由与 Agent 工具两种调用场景。
"""

import json
from datetime import datetime

from core.database import OWNER_ID, get_connection

# ---------------------------------------------------------------------------
# 画像字段定义
# ---------------------------------------------------------------------------
# 画像契约的全部可写字段（与前端 UserProfile 对齐），同时是写入白名单
CORE_FIELDS: tuple[str, ...] = (
    # 基本资料
    "name",
    "age",
    "city",
    "occupation",
    "industry",
    "education",
    # 财务状况
    "monthly_income",
    "monthly_expense",
    "assets",
    "debts",
    "goals",
    # 投资偏好
    "risk_tolerance",
    "investing_years",
    "liquidity_needs",
    # 家庭情况
    "family",
    "marital_status",
    "children",
    "supporting_parents",
)

# 资料完善度清单（12 项，与前端 PROFILE_CHECKLIST 对齐）
COMPLETION_FIELDS: tuple[str, ...] = (
    "name",
    "age",
    "city",
    "occupation",
    "industry",
    "education",
    "risk_tolerance",
    "investing_years",
    "liquidity_needs",
    "marital_status",
    "children",
    "supporting_parents",
)

# 首次设置（onboarding）必填字段：全部有效才算完成
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "age",
    "occupation",
    "monthly_income",
    "monthly_expense",
)


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------
def _now() -> str:
    """当前时间戳，用于 last_updated 字段。"""
    return datetime.now().isoformat(timespec="seconds")


def _default_profile() -> dict:
    """画像不存在时的默认值：数值字段为 0，集合字段为空。"""
    return {
        "user_id": "default",
        "name": "",
        "age": 0,
        "city": "",
        "occupation": "",
        "industry": "",
        "education": "",
        "family": "",
        "monthly_income": 0,
        "monthly_expense": 0,
        "assets": {},
        "debts": {},
        "goals": [],
        "risk_tolerance": "",
        "investing_years": 0,
        "liquidity_needs": "",
        "marital_status": "",
        "children": "",
        "supporting_parents": "",
        "last_updated": "",
    }


def _sanitize(data: dict) -> dict:
    """只保留契约内字段并剔除 None，防止非法字段入库。"""
    cleaned = {}
    for field in CORE_FIELDS:
        value = data.get(field)
        if value is not None:
            cleaned[field] = value
    return cleaned


def _is_filled(value) -> bool:
    """判断字段是否有效：字符串非空、集合非空、数值大于 0。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return len(value) > 0
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return value > 0
    return True


def _load_profile(conn) -> dict | None:
    """读取库中的画像 JSON；记录不存在或数据损坏时返回 None。"""
    row = conn.execute(
        "SELECT profile_data FROM financial_profiles WHERE id = ?",
        (OWNER_ID,),
    ).fetchone()
    if row is None or not row["profile_data"]:
        return None
    try:
        return json.loads(row["profile_data"])
    except json.JSONDecodeError:
        return None


def _save_profile(conn, profile: dict) -> None:
    """将画像写回数据库：不存在则插入，存在则更新。"""
    profile["last_updated"] = _now()
    conn.execute(
        """
        INSERT INTO financial_profiles
            (id, monthly_income, risk_tolerance, profile_data, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            monthly_income = excluded.monthly_income,
            risk_tolerance = excluded.risk_tolerance,
            profile_data   = excluded.profile_data,
            updated_at     = excluded.updated_at
        """,
        (
            OWNER_ID,
            profile.get("monthly_income", 0),
            profile.get("risk_tolerance", ""),
            json.dumps(profile, ensure_ascii=False),
            profile["last_updated"],
        ),
    )
    conn.commit()


# 对外接口
def get_profile() -> dict:
    """获取当前用户的财务画像；尚未创建时返回默认值。"""
    conn = get_connection()
    try:
        profile = _load_profile(conn)
    finally:
        conn.close()
    return profile if profile is not None else _default_profile()


def create_profile(data: dict) -> dict:
    """创建初始画像：默认值叠加传入字段；已存在则整体覆盖。"""
    profile = _default_profile()
    profile.update(_sanitize(data))
    profile["user_id"] = str(data.get("user_id") or f"user-{OWNER_ID}")

    conn = get_connection()
    try:
        _save_profile(conn, profile)
    finally:
        conn.close()
    return profile


def update_profile(data: dict) -> dict:
    """更新画像字段：浅合并到现有画像（assets/debts/goals 整体替换）。"""
    profile = get_profile()
    profile.update(_sanitize(data))
    if data.get("user_id") is not None:
        profile["user_id"] = str(data["user_id"])

    conn = get_connection()
    try:
        _save_profile(conn, profile)
    finally:
        conn.close()
    return profile


def get_profile_completion() -> float:
    """计算资料完善程度：完善度清单（12 项）已填写占比（0.0 ~ 1.0）。"""
    profile = get_profile()
    filled = sum(1 for field in COMPLETION_FIELDS if _is_filled(profile.get(field)))
    return round(filled / len(COMPLETION_FIELDS), 4)


def is_onboarding_completed() -> bool:
    """判断是否完成首次设置：必填字段全部有效。"""
    profile = get_profile()
    return all(_is_filled(profile.get(field)) for field in REQUIRED_FIELDS)
