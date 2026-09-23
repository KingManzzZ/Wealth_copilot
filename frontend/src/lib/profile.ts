import type { BasicInfo } from "@/types"

// ============================================================
// 基本信息工具：完善度统计、待确认项推导与量化提示
// 填写规则与后端 core/services/basic_info.py 的 _is_filled 保持一致
// 移除了原 monthly_income/monthly_expense（已从 cashflow 派生，不再属于 basic_info）
// ============================================================

/** 风险承受能力等级（算法估算，不可用户修改） */
export type RiskLevel = NonNullable<BasicInfo["risk_tolerance"]>

/** 投资偏好等级（算法估算，不可用户修改） */
export type InvestmentPreferenceLevel = NonNullable<BasicInfo["investment_preference"]>

/** 活期现金需求等级（算法估算，不可用户修改） */
export type LiquidityLevel = NonNullable<BasicInfo["liquidity_needs"]>

/** 个人资料清单：用于完善度统计与待确认项推导（13 项，含 3 个算法字段） */
export const PROFILE_CHECKLIST: { key: keyof BasicInfo; label: string }[] = [
  { key: "name", label: "姓名" },
  { key: "age", label: "年龄" },
  { key: "city", label: "城市" },
  { key: "occupation", label: "职业" },
  { key: "industry", label: "行业" },
  { key: "education", label: "教育程度" },
  { key: "risk_tolerance", label: "风险承受能力" },
  { key: "investment_preference", label: "投资偏好" },
  { key: "investing_years", label: "投资年限" },
  { key: "liquidity_needs", label: "活期现金需求" },
  { key: "marital_status", label: "婚姻状况" },
  { key: "children", label: "子女情况" },
  { key: "supporting_parents", label: "赡养父母情况" },
]

/** 风险承受能力展示文案 */
export const RISK_LABELS: Record<RiskLevel, string> = {
  conservative: "保守型",
  balanced: "平衡型",
  aggressive: "进取型",
}

/** 风险承受能力量化标准：权益类资产占比与最大回撤容忍 */
export const RISK_QUANTIFIED: Record<RiskLevel, string> = {
  conservative: "权益类资产占比 ≤ 20%，可承受最大回撤约 10%",
  balanced: "权益类资产占比 40%~60%，可承受最大回撤约 20%",
  aggressive: "权益类资产占比 ≥ 70%，可承受最大回撤约 30%",
}

/** 投资偏好展示文案 */
export const PREFERENCE_LABELS: Record<InvestmentPreferenceLevel, string> = {
  conservative: "保守型",
  balanced: "平衡型",
  aggressive: "进取型",
}

/** 投资偏好量化标准：主观投资风格倾向 */
export const PREFERENCE_QUANTIFIED: Record<InvestmentPreferenceLevel, string> = {
  conservative: "偏好固定收益类，权益类占比 ≤ 30%",
  balanced: "均衡配置，权益类占比 30%~60%",
  aggressive: "偏好成长型，权益类占比 ≥ 60%",
}

/** 活期现金需求展示文案 */
export const LIQUIDITY_LABELS: Record<LiquidityLevel, string> = {
  low: "较低",
  medium: "中等",
  high: "较高",
}

/** 活期现金需求对应需覆盖的月数 */
export const LIQUIDITY_MONTHS: Record<LiquidityLevel, number> = {
  low: 3,
  medium: 6,
  high: 12,
}

/** 字段是否已填写：字符串非空、数值大于 0 */
export function isFieldFilled(value: unknown): boolean {
  if (value === undefined || value === null) return false
  if (typeof value === "string") return value.trim() !== ""
  if (typeof value === "number") return value > 0
  return true
}

/** 资料完善度：已填字段占比（0~1） */
export function getProfileCompletion(info: BasicInfo): {
  filled: number
  total: number
  ratio: number
} {
  const total = PROFILE_CHECKLIST.length
  const filled = PROFILE_CHECKLIST.filter(({ key }) => isFieldFilled(info[key])).length
  return { filled, total, ratio: total === 0 ? 0 : filled / total }
}

/** 待确认项：清单中尚未填写的字段 */
export function getPendingConfirmations(info: BasicInfo): string[] {
  return PROFILE_CHECKLIST.filter(({ key }) => !isFieldFilled(info[key])).map(
    ({ label }) => `${label}待确认`
  )
}

/** 风险承受能力量化提示：供「?」悬浮展示 */
export function getRiskHint(info: BasicInfo): string {
  const level = info.risk_tolerance
  if (!level) return "尚未评估：系统将根据资产、收入稳定性、年龄等因素综合估算"
  return `当前「${RISK_LABELS[level]}」：${RISK_QUANTIFIED[level]}`
}

/** 投资偏好量化提示：供「?」悬浮展示 */
export function getInvestmentPreferenceHint(info: BasicInfo): string {
  const level = info.investment_preference
  if (!level) return "尚未评估：系统将根据用户投资行为和偏好问卷综合估算"
  return `当前「${PREFERENCE_LABELS[level]}」：${PREFERENCE_QUANTIFIED[level]}`
}

/** 活期现金需求量化提示：显示需覆盖月数（算法估算） */
export function getLiquidityHint(info: BasicInfo): string {
  const level = info.liquidity_needs
  if (!level) return "尚未评估：系统将根据收入稳定性、家庭负担等因素自动估算"
  const months = LIQUIDITY_MONTHS[level]
  return `当前「${LIQUIDITY_LABELS[level]}」：需覆盖 ${months} 个月日常支出`
}
