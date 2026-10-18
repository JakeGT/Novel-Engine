import type { CanonEntity } from '../api/client'
import { IMPORTANCE, importanceOf } from './marks'

/**
 * Naming an entry while writing (CTX-003): typing @ offers story-bible entries by name
 * and alias, and choosing one writes it in the marking the name finder already reads
 * (`backend/domain/sources.py`, `_MARK`): @Mara for one word, @"the Old Mill" for more.
 * Everything here is plain text matching; no model is involved.
 */

/** The @… being typed: where it starts and ends in the text, and the words after the @. */
export interface MentionQuery {
  start: number
  end: number
  text: string
  quoted: boolean
}

export interface MentionOption {
  id: string
  name: string
  type: string
  /** The words written in the text: the name, or the alias that matched. */
  label: string
  /** The alias that matched, when it was not the name. */
  via?: string
}

// Mirrors the server's marks: an @ not after a letter, digit, underscore or another @.
const QUOTED_BEFORE = /(?<![\p{L}\p{N}_@])@"([^"\n]{0,120})$/u
const WORD_BEFORE = /(?<![\p{L}\p{N}_@])@(\p{L}[\p{L}\p{N}_'’-]{0,60})?$/u
// One word the server reads after a bare @ (its `[^\W\d_][\w'’\-]{0,60}`).
const ONE_WORD = /^\p{L}[\p{L}\p{N}_'’-]{0,60}$/u
const WORD_REST = /^[\p{L}\p{N}_'’-]*/u
// The rest of a quoted name the caret is inside, through its closing quote: words joined
// by single spaces, with no other quote or sentence punctuation, the quote straight after
// a word and not before one. So in `@"Kes| He said "run."` the quote before "run" is not
// taken for the end of the name, and the words up to it are never replaced.
const QUOTED_REST = /^[^"\s.,;:!?…]*(?: [^"\s.,;:!?…]+)*"(?![\p{L}\p{N}_])/u

/**
 * The mention being typed at the caret, if there is one. It covers the word (or quoted
 * name) the caret is in, and `text` is that whole word, so what is replaced is always what
 * the names were matched against: typing @ just before "Mara" offers Mara, never a name
 * that would write over her.
 */
export function mentionAt(value: string, caret: number): MentionQuery | null {
  const before = value.slice(Math.max(0, caret - 130), caret)
  const quoted = QUOTED_BEFORE.exec(before)
  if (quoted) {
    const start = caret - quoted[0].length
    const after = value.slice(caret, caret + 121)
    const close = QUOTED_REST.exec(after)?.[0]
    const rest = close ?? WORD_REST.exec(after)?.[0] ?? ''
    const words = quoted[1] + (close ? rest.slice(0, -1) : rest)
    return { start, end: caret + rest.length, text: words, quoted: true }
  }
  const word = WORD_BEFORE.exec(before)
  if (!word) return null
  const start = caret - word[0].length
  const rest = WORD_REST.exec(value.slice(caret))?.[0] ?? ''
  return { start, end: caret + rest.length, text: (word[1] ?? '') + rest, quoted: false }
}

/** Lower case, without accents, single spaces: "Inés  Varga" and "ines varga" match. */
export function fold(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

/** How well a label matches what was typed: lower is better; null is no match. */
function score(label: string, typed: string): number | null {
  const folded = fold(label)
  if (!typed) return 4
  if (folded === typed) return 0
  if (folded.startsWith(typed)) return 1
  if (folded.split(' ').some((word) => word.startsWith(typed))) return 2
  if (folded.includes(typed)) return 3
  return null
}

const RANK = new Map(IMPORTANCE.map((level, index) => [level.value, index]))

type Entry = Pick<CanonEntity, 'id' | 'name' | 'type'> & Partial<Pick<CanonEntity, 'aliases' | 'lifecycle' | 'attributes'>>

/**
 * The entries that match what was typed, best first: an exact name, then names that
 * start with it, then any word of a name, then names that contain it; an alias counts
 * just after a name. With nothing typed yet, the most central entries come first.
 */
export function suggest(entries: Entry[], typed: string, limit = 8): MentionOption[] {
  const query = fold(typed)
  const found: { option: MentionOption; score: number; rank: number }[] = []
  for (const entry of entries) {
    if (entry.lifecycle && entry.lifecycle !== 'active') continue
    let best: { score: number; label: string; via?: string } | null = null
    const named = score(entry.name, query)
    if (named !== null) best = { score: named, label: entry.name }
    for (const alias of entry.aliases ?? []) {
      const aliased = score(alias, query)
      if (aliased !== null && (best === null || aliased + 0.5 < best.score)) {
        best = { score: aliased + 0.5, label: alias, via: alias }
      }
    }
    if (!best) continue
    found.push({
      option: { id: entry.id, name: entry.name, type: entry.type, label: best.label, via: best.via },
      score: best.score,
      rank: RANK.get(importanceOf({ attributes: entry.attributes ?? {} })) ?? RANK.size,
    })
  }
  return found
    .sort((a, b) => a.score - b.score || a.rank - b.rank || a.option.name.localeCompare(b.option.name))
    .slice(0, limit)
    .map((item) => item.option)
}

/** A name as a mark: @Mara, @"the Old Mill", or [[…]] when the name has a quotation mark. */
export function formatMention(label: string): string {
  const name = label.replace(/\s+/g, ' ').trim()
  if (ONE_WORD.test(name)) return `@${name}`
  if (!name.includes('"') && name.length <= 120) return `@"${name}"`
  if (!/[[\]]/.test(name) && name.length <= 120) return `[[${name}]]`
  return name
}

const KEEPS_SPACE = /^[\s.,;:!?…)\]}’”"'—–-]/u

/** The text with the mention written in, and where the caret goes after it. */
export function insertMention(
  value: string,
  query: Pick<MentionQuery, 'start' | 'end'>,
  label: string,
): { value: string; caret: number; inserted: string } {
  const after = value.slice(query.end)
  // A space after it, so the next word can follow, unless punctuation or a space is there.
  const inserted = formatMention(label) + (KEEPS_SPACE.test(after) ? '' : ' ')
  return {
    value: value.slice(0, query.start) + inserted + after,
    caret: query.start + inserted.length,
    inserted,
  }
}
