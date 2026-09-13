def cal_monthly_cashflow(monthly_income:float, monthly_expense:float):

    surplus=monthly_income-monthly_expense

    return {
        "monthly_income":monthly_income,
        "monthly_expense":monthly_expense,
        "surplus":surplus,
        "saving_rate":surplus/monthly_income
    }