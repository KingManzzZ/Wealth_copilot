TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_monthly_cashflow",
            "description": "计算用户的月度现金流和储蓄率",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_income": {
                        "type": "number",
                        "description": "月收入"
                    },
                    "monthly_expense": {
                        "type": "number",
                        "description": "月支出"
                    }
                },
                "required": [
                    "monthly_income",
                    "monthly_expense"
                ]
            }
        }
    }
]