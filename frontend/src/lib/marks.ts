// Marks on an entry that help an author find and rank it: tags, how central it is
// to the story, and whether it is waiting to be filled in. They are stored beside
// an entry's details under reserved names (see backend/domain/canon/marks.py), and
// are never shown as details.
import type { CanonEntity } from '../api/client'

export const TAGS_KEY = '_tags'
export const IMPORTANCE_KEY = '_importance'
export const NEEDS_DETAIL_KEY = '_needs_detail'
const MARK_KEYS = new Set([TAGS_KEY, IMPORTANCE_KEY, NEEDS_DETAIL_KEY])

/** Most to least central, with the words an author sees. */
export const IMPORTANCE: { value: string; label: string; hint: string }[] = [
  { value: 'main', label: 'Main', hint: 'The story is about them' },
  { value: 'supporting', label: 'Supporting', hint: 'Matters to the story throughout' },
  { value: 'minor', label: 'Minor', hint: 'Appears, but the story does not turn on them' },
  { value: 'background', label: 'Background', hint: 'Mentioned, or part of the setting' },
]

type Attributes = Record<string, unknown> | null | undefined

export function isMark(key: string): boolean {
  return MARK_KEYS.has(key)
}

/** An entry's own details, without the marks. */
export function detailsOf(attributes: Attributes): Record<string, unknown> {
  return Object.fromEntries(Object.entries(attributes ?? {}).filter(([key]) => !MARK_KEYS.has(key)))
}

export function marksOf(attributes: Attributes): Record<string, unknown> {
  return Object.fromEntries(Object.entries(attributes ?? {}).filter(([key]) => MARK_KEYS.has(key)))
}

export function tagsOf(entity: Pick<CanonEntity, 'attributes'>): string[] {
  const tags = entity.attributes?.[TAGS_KEY]
  return Array.isArray(tags) ? tags.filter((tag): tag is string => typeof tag === 'string') : []
}

export function importanceOf(entity: Pick<CanonEntity, 'attributes'>): string {
  const value = entity.attributes?.[IMPORTANCE_KEY]
  return typeof value === 'string' && IMPORTANCE.some((item) => item.value === value) ? value : ''
}

export function importanceLabel(value: string): string {
  return IMPORTANCE.find((item) => item.value === value)?.label ?? ''
}

export function needsDetail(entity: Pick<CanonEntity, 'attributes'>): boolean {
  return entity.attributes?.[NEEDS_DETAIL_KEY] === true
}

/** "protagonist, John's professor" → ["protagonist", "John's professor"]. */
export function parseTags(text: string): string[] {
  const seen = new Set<string>()
  return text
    .split(',')
    .map((tag) => tag.replace(/\s+/g, ' ').trim())
    .filter((tag) => {
      if (!tag || tag.length > 40 || seen.has(tag.toLowerCase())) return false
      seen.add(tag.toLowerCase())
      return true
    })
    .slice(0, 6)
}

// Unranked entries come after the ones an author has called minor, and before
// background: not knowing how much something matters is not the same as it not mattering.
const RANK: Record<string, number> = { main: 0, supporting: 1, minor: 2, '': 3, background: 4 }

/**
 * The order lists are shown in: the entries that matter most to the story first,
 * then the most recently added. Alphabetical order says nothing about a story.
 */
export function byPriority(a: CanonEntity, b: CanonEntity): number {
  return (
    RANK[importanceOf(a)] - RANK[importanceOf(b)] ||
    b.created_revision - a.created_revision ||
    a.name.localeCompare(b.name)
  )
}
