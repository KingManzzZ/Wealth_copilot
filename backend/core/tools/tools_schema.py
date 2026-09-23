"""
OpenAI Function Calling Schema 定义。

只声明工具的调用契约（名字 + 参数）；实际路由由 tools_route.py 完成。
按业务模块分组，方便对照 tools_hub/*.py 中的实现。
"""

TOOLS_SCHEMA = [
    # =========================================================
    # 1. 基本信息工具（basic_info_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "get_financial_profile",
            "description": "获取当前用户完整财务画像：基本信息 + 资产 + 债务 + 目标聚合视图。无需参数。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_basic_info",
            "description": "更新基本信息字段（仅 basic_info 表的用户可编辑字段；assets/debts/goals 走各自工具）。risk_tolerance / investment_preference / liquidity_needs 为算法估算字段，不可通过此工具修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "姓名"},
                    "age": {"type": "number", "description": "年龄"},
                    "city": {"type": "string", "description": "所在城市"},
                    "occupation": {"type": "string", "description": "职业"},
                    "industry": {"type": "string", "description": "所属行业"},
                    "education": {"type": "string", "description": "教育程度"},
                    "family": {"type": "string", "description": "家庭状况"},
                    "marital_status": {"type": "string", "description": "婚姻状况"},
                    "children": {"type": "string", "description": "子女情况"},
                    "supporting_parents": {"type": "string", "description": "赡养父母情况"},
                    "investing_years": {"type": "number", "description": "投资年限"},
                },
                "required": [],
            },
        },
    },

    # =========================================================
    # 2. 资产工具（asset_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "get_assets",
            "description": "列出当前用户全部资产，返回 {name: value} 字典。无需参数。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_asset",
            "description": "新增资产；同名资产已存在则更新估值（UPSERT）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "资产名称（如'现金及活期'、'基金'、'股票'）"},
                    "value": {"type": "number", "description": "当前估值（元）"},
                },
                "required": ["name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_asset",
            "description": "更新资产估值；语义上与 record_asset 等价（底层都是 UPSERT）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "资产名称"},
                    "value": {"type": "number", "description": "新的估值（元）"},
                },
                "required": ["name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_net_worth",
            "description": "计算净资产 = 资产总额 - 负债总额。无需参数。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_emergency_fund",
            "description": "计算应急资金目标与缺口。规则：月数由 liquidity_needs 决定（low=3/medium=6/high=12），必要月支出取最近月 cashflow。无需参数。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    # =========================================================
    # 3. 债务工具（debt_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "get_debts",
            "description": "列出当前用户全部负债，返回 {name: {remaining, monthly_payment}} 字典。无需参数。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_debt",
            "description": "新增负债；同名已存在则更新（UPSERT）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "负债名称（如'房贷'、'车贷'、'信用卡'）"},
                    "remaining": {"type": "number", "description": "剩余本金（元）"},
                    "monthly_payment": {"type": "number", "description": "月还款额（元）"},
                },
                "required": ["name", "remaining", "monthly_payment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_debt",
            "description": "更新负债余额或月还款；语义上与 record_debt 等价。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "负债名称"},
                    "remaining": {"type": "number", "description": "新的剩余本金（元）"},
                    "monthly_payment": {"type": "number", "description": "新的月还款额（元）"},
                },
                "required": ["name", "remaining", "monthly_payment"],
            },
        },
    },

    # =========================================================
    # 4. 财富目标工具（goal_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "create_goal",
            "description": "创建财富目标；goal_id 已存在则整体覆盖（UPSERT）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "目标唯一标识（如 'g1'）"},
                    "name": {"type": "string", "description": "目标名称"},
                    "target": {"type": "number", "description": "目标金额（元）"},
                    "current": {"type": "number", "description": "当前已积累金额（元，默认 0）"},
                    "deadline": {"type": "string", "description": "截止日期 'YYYY-MM-DD'"},
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "优先级，默认 'medium'",
                    },
                },
                "required": ["id", "name", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_goals",
            "description": "列出财富目标；可按 status 筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "completed", "paused"],
                        "description": "可选状态筛选；不传则返回全部",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal",
            "description": "修改目标字段。注意：底层是 UPSERT，未传字段会被默认值覆盖；若要保留原值请先 get_goals 取出再合并。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "目标唯一标识"},
                    "name": {"type": "string", "description": "新的目标名称"},
                    "target": {"type": "number", "description": "新的目标金额"},
                    "current": {"type": "number", "description": "新的当前积累金额"},
                    "deadline": {"type": "string", "description": "新的截止日期 'YYYY-MM-DD'"},
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "新的优先级",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_goal_tool",
            "description": "将目标标记为已完成；记录不存在返回 {\"error\": ...}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string", "description": "目标唯一标识"},
                },
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_goal_contribution",
            "description": "计算达成目标所需月储蓄额 = (target - current) / 剩余月数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string", "description": "目标唯一标识"},
                },
                "required": ["goal_id"],
            },
        },
    },

    # =========================================================
    # 5. 交易流水工具（transaction_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "record_income",
            "description": "记录一笔收入流水。",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "金额（元，必须 > 0）"},
                    "category": {"type": "string", "description": "收入分类（如'薪资'、'奖金'、'理财收益'）"},
                    "occurred_at": {"type": "string", "description": "发生时间 ISO 8601（如 '2026-09-23T12:00:00'）"},
                    "source": {"type": "string", "description": "来源说明（可选，如'9月工资'）"},
                    "note": {"type": "string", "description": "备注（可选）"},
                },
                "required": ["amount", "category", "occurred_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_expense",
            "description": "记录一笔支出流水。",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "金额（元，必须 > 0）"},
                    "category": {"type": "string", "description": "支出分类（如'餐饮'、'交通'、'房租'、'房贷'）"},
                    "occurred_at": {"type": "string", "description": "发生时间 ISO 8601"},
                    "source": {"type": "string", "description": "用途说明（可选，如'午餐'）"},
                    "note": {"type": "string", "description": "备注（可选）"},
                },
                "required": ["amount", "category", "occurred_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transactions",
            "description": "查询交易流水，按时间倒序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "按月份筛选 'YYYY-MM'；不传则返回全部",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_transaction_tool",
            "description": "修改已有流水；只传需要修改的字段。记录不存在返回 None。",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "number", "description": "流水 ID"},
                    "amount": {"type": "number", "description": "新的金额"},
                    "category": {"type": "string", "description": "新的分类"},
                    "source": {"type": "string", "description": "新的来源"},
                    "occurred_at": {"type": "string", "description": "新的发生时间"},
                    "note": {"type": "string", "description": "新的备注"},
                    "transaction_type": {
                        "type": "string",
                        "enum": ["income", "expense"],
                        "description": "新的类型（一般不改）",
                    },
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_transaction_tool",
            "description": "删除流水；返回 {ok, deleted_id}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "number", "description": "流水 ID"},
                },
                "required": ["transaction_id"],
            },
        },
    },

    # =========================================================
    # 6. 现金流工具（cashflow_tools.py）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "get_monthly_cashflow",
            "description": "获取月度现金流汇总。months=1 返回最近一个月 dict；>1 返回 list。",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {
                        "type": "number",
                        "description": "取最近 N 个月，默认 1",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_savings_rate",
            "description": "计算储蓄率 = (income - expense) / income。不传 month 则取最近一个月。",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "指定月份 'YYYY-MM'；不传则取最近一个月",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_monthly_cashflow",
            "description": "UPSERT 某月现金流汇总（主要用于测试或数据补录）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {"type": "string", "description": "月份 'YYYY-MM'"},
                    "income": {"type": "number", "description": "月收入合计（元）"},
                    "expense": {"type": "number", "description": "月支出合计（元）"},
                },
                "required": ["month", "income", "expense"],
            },
        },
    },
]
