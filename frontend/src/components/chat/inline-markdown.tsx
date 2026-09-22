import * as React from "react"

/**
 * 极简内联 markdown 渲染
 * 支持：**bold**、换行（whitespace-pre-wrap）
 * 不引入 react-markdown，保持体积小、可控
 */
export function InlineMarkdown({ content }: { content: string }) {
  return (
    <div className="whitespace-pre-wrap leading-relaxed">
      {parseInline(content)}
    </div>
  )
}

function parseInline(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      )
    }
    return <span key={i}>{part}</span>
  })
}
