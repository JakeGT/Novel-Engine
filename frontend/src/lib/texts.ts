import type { TextMention } from '../api/client'

export interface Segment {
  start: number
  text: string
  mention?: TextMention
  /** Inside the passage a change pointed at. */
  cited: boolean
}

/**
 * A stretch of the text cut into plain words, the names found in them, and the
 * passage a change pointed at, if one was asked for.
 */
export function segments(
  content: string,
  mentions: TextMention[],
  from: number,
  to: number,
  cite?: { start: number; end: number } | null,
): Segment[] {
  const inside = mentions.filter((mention) => mention.start >= from && mention.end <= to)
  const cuts = new Set<number>([from, to])
  for (const mention of inside) {
    cuts.add(mention.start)
    cuts.add(mention.end)
  }
  if (cite) {
    for (const edge of [cite.start, cite.end]) if (edge > from && edge < to) cuts.add(edge)
  }
  const edges = [...cuts].sort((a, b) => a - b)
  const found: Segment[] = []
  for (let index = 0; index + 1 < edges.length; index += 1) {
    const [start, end] = [edges[index], edges[index + 1]]
    found.push({
      start,
      text: content.slice(start, end),
      mention: inside.find((mention) => mention.start <= start && end <= mention.end),
      cited: Boolean(cite && cite.start <= start && end <= cite.end),
    })
  }
  return found
}

/** A rough count, good enough to say how long something is. */
export function wordCount(content: string): number {
  return content.trim() ? content.trim().split(/\s+/).length : 0
}

/** Roughly how many words a number of characters holds, when the words are not to hand. */
export function wordsFromLength(length: number): number {
  return Math.round(length / 6 / 10) * 10
}

const LOCATION = /^chars:(\d+)-(\d+)(,implied)?$/

export function parseLocation(location: string | null | undefined) {
  const match = LOCATION.exec(location ?? '')
  return match ? { start: Number(match[1]), end: Number(match[2]), implied: Boolean(match[3]) } : null
}

/** Evidence that points at a version of one of the author's texts. */
export function isTextEvidence(sourceId: string | null | undefined): boolean {
  return Boolean(sourceId?.startsWith('srv_'))
}
