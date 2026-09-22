import { useEffect, useState } from "react"

type Theme = "light" | "dark"
const STORAGE_KEY = "wealthpilot-theme"

/**
 * 双主题 hook：读写 localStorage + 切换 <html>.dark
 * 默认跟随系统偏好
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") return "light"
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null
    if (stored) return stored
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light"
  })

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle("dark", theme === "dark")
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"))

  return { theme, setTheme, toggle }
}
