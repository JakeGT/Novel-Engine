// The story plan as an outline: what sits under what, in the author's order.
import type { PlanItem, PlanKind } from '../api/client'

/** What may be added under each kind of plan item (and at the top, under nothing). */
export const CHILD_KINDS: Record<string, PlanKind[]> = {
  '': ['story', 'arc', 'beat', 'scene'],
  story: ['arc', 'beat', 'scene'],
  arc: ['beat', 'scene'],
  beat: ['scene'],
  scene: [],
  thread: [],
}

export interface OutlineNode {
  item: PlanItem
  depth: number
  children: OutlineNode[]
}

const inOrder = (a: PlanItem, b: PlanItem) =>
  a.position - b.position || a.created_revision - b.created_revision || a.id.localeCompare(b.id)

// At the top level a story comes before loose arcs, beats and scenes.
const TOP_ORDER = ['story', 'arc', 'beat', 'scene']

/** The outline as a tree (threads are kept apart: they run across it). */
export function outlineTree(items: PlanItem[]): OutlineNode[] {
  const parts = items.filter((item) => item.kind !== 'thread')
  const known = new Set(parts.map((item) => item.id))
  const under = new Map<string, PlanItem[]>()
  for (const item of parts) {
    // Something whose parent is not shown is listed at the top rather than lost.
    const key = item.parent_id && known.has(item.parent_id) ? item.parent_id : ''
    under.set(key, [...(under.get(key) ?? []), item])
  }
  const build = (parent: string, depth: number): OutlineNode[] =>
    (under.get(parent) ?? [])
      .sort((a, b) =>
        parent === '' ? TOP_ORDER.indexOf(a.kind) - TOP_ORDER.indexOf(b.kind) || inOrder(a, b) : inOrder(a, b),
      )
      .map((item) => ({ item, depth, children: build(item.id, depth + 1) }))
  return build('', 0)
}

export function threadsOf(items: PlanItem[]): PlanItem[] {
  return items.filter((item) => item.kind === 'thread').sort(inOrder)
}

/** The items an item sits beside, in order: same parent, and at the top the same kind. */
export function siblingsOf(items: PlanItem[], item: PlanItem): PlanItem[] {
  return items
    .filter(
      (other) =>
        (other.parent_id ?? null) === (item.parent_id ?? null) &&
        (item.parent_id ? other.kind !== 'thread' : other.kind === item.kind),
    )
    .sort(inOrder)
}

/**
 * How a scene ends for its viewpoint character: the four answers of the common
 * goal–conflict–outcome pattern of scene writing (Swain; Bickham, Scene & Structure).
 */
export const OUTCOMES: { value: 'yes' | 'no' | 'yes_but' | 'no_and'; label: string; hint: string }[] = [
  { value: 'yes_but', label: 'Yes, but', hint: 'they get it, with a cost or a new complication' },
  { value: 'no_and', label: 'No, and', hint: 'they do not get it, and things get worse' },
  { value: 'yes', label: 'Yes', hint: 'they get what they want' },
  { value: 'no', label: 'No', hint: 'they do not get it' },
]

export const outcomeLabel = (value: string | null | undefined): string | null =>
  OUTCOMES.find((outcome) => outcome.value === value)?.label ?? null

/** What each step of a thread is called on screen. */
export const THREAD_STEPS: Record<string, string> = {
  opens: 'Opens',
  advances: 'Moves along',
  resolves: 'Resolves',
}
