/**
 * 把正文里的 `[1]` 变成一个可点击的引用 chip。
 *
 * ## 为什么在 mdast 上做，而不是正则替换原文
 *
 * 直接 `content.replace(/\[(\d+)\]/g, "<span>…")` 会误伤三类内容：
 * 1. 链接 `[文本](url)` 的方括号；
 * 2. GFM 任务列表的复选框 `- [ ] 待办`；
 * 3. 行内代码 `` `[1]` `` / 代码块里本应原样显示的字面量。
 *
 * 在 markdown 语法树上只处理 **text 节点**，并跳过 link / inlineCode / code 的子树，
 * 上面三类天然不在作用域内。
 *
 * 产出的节点用 `data.hName` 指定渲染成 `<span class="citation-chip" data-cite="1">`，
 * 点击由外层容器做事件委托（见 `components/chat/markdown.tsx`），
 * 这样不必为 markdown 内部的每个内联元素注册组件。
 */

import type { Plugin } from "unified"

interface MdNode {
  type: string
  value?: string
  children?: MdNode[]
  data?: Record<string, unknown>
}

export interface CitationOptions {
  /**
   * 该编号是否真的有对应证据。
   * 返回 false 时保持原文 —— 与其让用户点开一个空抽屉，不如让它看起来就是普通文字。
   */
  hasEvidence?: (index: number) => boolean
}

/** 这些节点的子树不做替换：链接文字、行内代码、代码块、原始 HTML */
const SKIP_SUBTREE = new Set(["link", "inlineCode", "code", "html", "definition"])

/** `[1]` ~ `[999]`；更长的编号（如 `[2026]`）按普通文字处理 */
const CITATION_PATTERN = /\[(\d{1,3})\]/g

export function remarkCitations(options: CitationOptions = {}): Plugin {
  const hasEvidence = options.hasEvidence ?? (() => true)

  return () => (tree) => {
    const root = tree as unknown as MdNode
    if (root?.type === "root") walk(root, hasEvidence)
  }
}

function walk(node: MdNode, hasEvidence: (index: number) => boolean): void {
  if (!node.children || SKIP_SUBTREE.has(node.type)) return

  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type === "text" && child.value) {
      const replaced = splitCitations(child.value, hasEvidence)
      if (replaced) {
        next.push(...replaced)
        continue
      }
    }
    walk(child, hasEvidence)
    next.push(child)
  }
  node.children = next
}

/** 把一段纯文本按 `[n]` 切开；没有可替换的编号时返回 null（保持原节点不变） */
function splitCitations(
  text: string,
  hasEvidence: (index: number) => boolean
): MdNode[] | null {
  const out: MdNode[] = []
  let cursor = 0
  let replaced = false

  for (const match of text.matchAll(CITATION_PATTERN)) {
    const index = Number(match[1])
    if (!Number.isInteger(index) || index < 1 || !hasEvidence(index)) continue

    const start = match.index ?? 0
    if (start > cursor) {
      out.push({ type: "text", value: text.slice(cursor, start) })
    }
    out.push({
      type: "citation",
      value: String(index),
      data: {
        hName: "span",
        hProperties: {
          className: "citation-chip",
          "data-cite": String(index),
          role: "button",
          tabIndex: 0,
        },
      },
    })
    cursor = start + match[0].length
    replaced = true
  }

  if (!replaced) return null
  if (cursor < text.length) {
    out.push({ type: "text", value: text.slice(cursor) })
  }
  return out
}
