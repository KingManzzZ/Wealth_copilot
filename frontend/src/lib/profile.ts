import type { UserProfile } from "@/types"
import { formatCurrency } from "@/lib/utils"

// ============================================================
// 财务画像工具：完善度统计、待确认项推导与量化提示
// 填写规则与后端 core/services/profile.py 的 _is_filled 保持一致
// ============================================================

/** 风险承受能力等级 */
export type RiskLevel = NonNullable<UserProfile["risk_tolerance"]>

/** 活期现金需求等级 */
export type LiquidityLevel = NonNullable<UserProfile["liquidity_needs"]>

/** 个人资料清单：用于完善度统计与待确认项推导 */
export const PROFILE_CHECKLIST: { key: keyof UserProfile; label: string }[] = [
  { key: "name", label: "姓名" },
  { key: "age", label: "年龄" },
  { key: "city", label: "城市" },
  { key: "occupation", label: "职业" },
  { key: "industry", label: "行业" },
  { key: "education", label: "教育程度" },
  { key: "risk_tolerance", label: "风险等级" },
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

/** 风险等级量化标准：权益类资产占比与最大回撤容忍 */
export const RISK_QUANTIFIED: Record<RiskLevel, string> = {
  conservative: "权益类资产占比 ≤ 20%，可承受最大回撤约 10%",
  balanced: "权益类资产占比 40%~60%，可承受最大回撤约 20%",
  aggressive: "权益类资产占比 ≥ 70%，可承受最大回撤约 30%",
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
export function getProfileCompletion(profile: UserProfile): {
  filled: number
  total: number
  ratio: number
} {
  const total = PROFILE_CHECKLIST.length
  const filled = PROFILE_CHECKLIST.filter(({ key }) => isFieldFilled(profile[key])).length
  return { filled, total, ratio: total === 0 ? 0 : filled / total }
}

/** 待确认项：清单中尚未填写的字段 */
export function getPendingConfirmations(profile: UserProfile): string[] {
  return PROFILE_CHECKLIST.filter(({ key }) => !isFieldFilled(profile[key])).map(
    ({ label }) => `${label}待确认`
  )
}

/** 风险等级量化提示：供「?」悬浮展示 */
export function getRiskHint(profile: UserProfile): string {
  const level = profile.risk_tolerance
  if (!level) return "尚未评估：完成风险测评后，这里会显示对应的量化标准"
  return `当前「${RISK_LABELS[level]}」：${RISK_QUANTIFIED[level]}`
}

/** 活期现金需求量化提示：按当前月支出估算建议保留金额 */
export function getLiquidityHint(profile: UserProfile): string {
  const level = profile.liquidity_needs
  if (!level) return "尚未设置：设定后按需覆盖的月数保留活期现金"
  const months = LIQUIDITY_MONTHS[level]
  const expense = profile.monthly_expense ?? 0
  if (expense > 0) {
    return `当前「${LIQUIDITY_LABELS[level]}」：需覆盖 ${months} 个月支出，按当前月支出 ${formatCurrency(expense)} 估算约 ${formatCurrency(months * expense)}`
  }
  return `当前「${LIQUIDITY_LABELS[level]}」：需覆盖 ${months} 个月日常支出`
}
