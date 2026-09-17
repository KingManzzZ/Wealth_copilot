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
 */
export function formatCurrency(value: number, withSymbol = true): string {
  const prefix = withSymbol ? "¥" : ""
  return `${prefix}${value.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
  })}`
}

/**
 * formatPercent: 0.4 -> "40.0%"
 */
export function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`
}
