/**
 * 从一条 assistant 消息里挖出「知识库证据」与「回答可靠性状态」。
 *
 * 数据来源就是 `search_knowledge` 的工具返回值（`message.toolCalls[].result`），
 * 字段契约见 `core/tools/tools_hub/rag_tools/impl.py`：
 *
 * ```json
 * {
 *   "evidence_quality": "high|medium|low",
 *   "directive": "answer|answer_with_caveat|abstain",
 *   "query_used": "…",
 *   "results": [{ "index": 1, "citation": "文档 | 页 | 标题路径", "text": "正文" }],
 *   "notice": "…"
 * }
 * ```
 *
 * ## 为什么按「每次检索」分组，而不是合并成一张表
 *
 * `index` 由后端**每次检索单独编号**（都从 1 开始）。一轮里检索两次时，
 * `[1]` 指的是哪一条并不唯一 —— 合并会伪造出一种"这些编号是全剧唯一"的假象。
 * 分组保留这个事实：抽屉里按检索次数分开列，用户看到的是"两次检索各自的第一条"。
 */

import type { ChatMessage, ToolCall } from "@/types"

export const SEARCH_TOOL = "search_knowledge"

export type EvidenceQuality = "high" | "medium" | "low"

export interface EvidenceItem {
  /** 与正文里的 `[n]` 对应 */
  index: number
  /** 出处：文档 | 页码 | 标题路径 */
  citation: string
  /** 证据正文（已由后端裁剪到上下文预算内） */
  text: string
  /** 可选的父节全文（仅 include_parent 时存在） */
  parentText?: string
}

/** 一次检索对应的证据集合 */
export interface EvidenceGroup {
  toolCallId: string
  /** 实际参与检索的查询串（后端回显的 query_used） */
  query?: string
  quality?: EvidenceQuality
  directive?: string
  notice?: string
  items: EvidenceItem[]
}

/** 一条消息的知识库状态，用于渲染独立提示态 */
export type KnowledgeState =
  | { kind: "none" }
  | { kind: "ok" }
  | { kind: "low"; notice?: string }
  | { kind: "abstain"; notice?: string }

type Payload = Record<string, unknown>

/** 工具返回值可能是对象，也可能是 JSON 字符串（历史消息回放时会被 stringify） */
function asPayload(result: unknown): Payload | null {
  if (result == null) return null
  let value: unknown = result
  if (typeof value === "string") {
    try {
      value = JSON.parse(value)
    } catch {
      return null
    }
  }
  if (typeof value !== "object" || value === null) return null
  return value as Payload
}

function payloadOf(toolCall: ToolCall): Payload | null {
  return toolCall.name === SEARCH_TOOL ? asPayload(toolCall.result) : null
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined
}

/** 收集一条消息里所有检索结果，按调用顺序返回（没有检索时返回空数组） */
export function collectEvidence(message: ChatMessage): EvidenceGroup[] {
  const groups: EvidenceGroup[] = []

  for (const toolCall of message.toolCalls ?? []) {
    const payload = payloadOf(toolCall)
    if (!payload) continue

    const rawResults = Array.isArray(payload.results) ? payload.results : []
    const items: EvidenceItem[] = []
    for (const raw of rawResults) {
      if (typeof raw !== "object" || raw === null) continue
      const item = raw as Payload
      const index = Number(item.index)
      const citation = asString(item.citation)
      const text = asString(item.text)
      if (!Number.isInteger(index) || index < 1 || !citation || !text) continue
      items.push({
        index,
        citation,
        text,
        parentText: asString(item.parent_text),
      })
    }

    groups.push({
      toolCallId: toolCall.id,
      query: asString(payload.query_used) ?? asString(toolCall.arguments?.query),
      quality: normalizeQuality(payload.evidence_quality),
      directive: asString(payload.directive),
      notice: asString(payload.notice),
      items,
    })
  }

  return groups
}

function normalizeQuality(value: unknown): EvidenceQuality | undefined {
  return value === "high" || value === "medium" || value === "low"
    ? value
    : undefined
}

/** 消息正文里 `[n]` 能对上的编号集合（用于决定要不要渲染成可点的 chip） */
export function evidenceIndexes(message: ChatMessage): Set<number> {
  const indexes = new Set<number>()
  for (const group of collectEvidence(message)) {
    for (const item of group.items) indexes.add(item.index)
  }
  return indexes
}

/**
 * 一条消息的知识库状态。
 *
 * 聚合规则刻意用「全部」而不是「任意」：一轮里检索两次、只有一次没查到时，
 * 若按「任意一次 low 就告警」，几乎每轮都会出现黄标 —— 而模型可能正是靠第二次
 * 检索补齐的依据。只有**所有**检索都没有可靠依据时，才提示用户"这轮没查到"。
 */
export function knowledgeState(message: ChatMessage): KnowledgeState {
  const directives: string[] = []
  const qualities: EvidenceQuality[] = []
  let notice: string | undefined

  for (const toolCall of message.toolCalls ?? []) {
    const payload = payloadOf(toolCall)
    if (!payload) continue
    const directive = asString(payload.directive)
    if (directive) directives.push(directive)
    const quality = normalizeQuality(payload.evidence_quality)
    if (quality) qualities.push(quality)
    if (!notice) notice = asString(payload.notice)
  }

  if (directives.length === 0 && qualities.length === 0) return { kind: "none" }

  const allAbstain =
    directives.length > 0 && directives.every((d) => d === "abstain")
  if (allAbstain) return { kind: "abstain", notice }

  const allLow =
    qualities.length > 0 && qualities.every((q) => q === "low")
  if (allLow) return { kind: "low", notice }

  return { kind: "ok" }
}
