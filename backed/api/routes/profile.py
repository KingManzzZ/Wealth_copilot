from fastapi import APIRouter

router = APIRouter()

PROFILE = PROFILE = { "user_id" : "demo-001" , 
                      "name" : "学习者" , 
                      "age" : 29 , 
                      "occupation" : "后端工程师" , 
                      "family" : "单身" , 
                      "monthly_income" : 18000 , 
                      "monthly_expense" : 9500 , 
                      "assets" : { "现金及活期" : 80000 , "基金" : 30000 , "股票" : 20000 }, 
                      "debts" : { "房贷" : { "remaining" : 800000 , "monthly_payment" : 6500 }}, 
                      "goals" : [
                    { "id" : "g1" , "name" : "博士学费" , "target" : 500000 , "current" : 130000 , "deadline" : "2031-09-16" , "priority" : "high" },
                    { "id" : "g2" , "name" : "应急资金（6 个月支出）" , "target" : 57000 , "current" : 42000 , "deadline" : "2027-03-16" , "priority" : "medium" },
                    { "id" : "g3" , "name" : "年度旅行基金" , "target" : 20000 , "current" : 6000 , "deadline" : "2027-06-16" , "priority" : "low" },
            ], 
                    "risk_tolerance" : "balanced" , 
                    "last_updated" : "2026-09-16" ,
}

def get_profile():
    return PROFILE

@router.get("/profile")
async def profile():
    return PROFILE

