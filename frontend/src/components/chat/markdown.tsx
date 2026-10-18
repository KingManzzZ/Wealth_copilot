import * as React from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import { remarkCitations } from "@/lib/remark-citations"

/**
 * Agent 回答的 markdown 渲染。
 *
 * ## 为什么不再自己写行内解析
 *
 * 之前的 `InlineMarkdown` 只认 `**bold**`，而 Agent 的实际输出里有表格、多级列表、
 * 引用块 —— 全部退化成一行行带 `|` 和 `-` 的纯文本，等于把排版工作丢给用户去脑补。
 *
 * ## 为什么引用 chip 用事件委托
 *
 * `[n]` 会被 remark 插件替换成 `<span data-cite="n">`。如果为它注册
 * `components.span`，会连带影响所有 span，而且 `p / li / td` 里的 chip 还得逐个处理。
 * 直接在容器上监听点击、用 `closest("[data-cite]")` 找目标，一处就覆盖全部上下文。
 *
 * 顺带：chip 是 `role="button"` 的 span，所以这里补了 Enter/Space 键盘激活。
 */
export function Markdown({
  content,
  hasCitation,
  onCitation,
}: {
  content: string
  /** 该编号是否有对应证据；没有就保持普通文字 */
  hasCitation: (index: number) => boolean
  onCitation: (index: number) => void
}) {
  const plugins = React.useMemo(
    () => [remarkGfm, remarkCitations({ hasEvidence: hasCitation })],
    [hasCitation]
  )

  const activate = (target: EventTarget | null) => {
    const el = (target as HTMLElement | null)?.closest?.(
      "[data-cite]"
    ) as HTMLElement | null
    if (!el) return
    const index = Number(el.dataset.cite)
    if (Number.isInteger(index) && index > 0) onCitation(index)
  }

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    activate(event.target)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return
    const el = (event.target as HTMLElement | null)?.closest?.(
      "[data-cite]"
    ) as HTMLElement | null
    if (!el) return
    event.preventDefault()
    activate(event.target)
  }

  return (
    <div
      className="wp-markdown"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      <ReactMarkdown remarkPlugins={plugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

const components: Components = {
  // 外链一律新开：聊天面板是常驻区，跳走会丢掉整段对话上下文
  a: ({ node: _node, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer noopener" />
  ),
}
