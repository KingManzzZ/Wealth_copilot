import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * cn: 合并 className 并解决 Tailwind 类冲突。
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * formatCurrency: 把数字格式化为人民币展示字符串。
 * 1234.5 -> "¥1,234.5"
 * NaN / 非有限数 -> "¥0"
 */
export function formatCurrency(value: number, withSymbol = true): string {
  const prefix = withSymbol ? "¥" : ""
  if (!Number.isFinite(value)) return `${prefix}0`
  return `${prefix}${value.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
  })}`
}

/**
 * formatPercent: 0.4 -> "40.0%"
 * NaN / 非有限数 -> "0.0%"
 */
export function formatPercent(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return `${(0).toFixed(digits)}%`
  return `${(value * 100).toFixed(digits)}%`
}
