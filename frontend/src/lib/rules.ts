import type { RuleBody, RulePattern } from '../api/client'

export type RuleCheckShape = RuleBody['checks'][number]
export type CheckKind = RuleCheckShape['kind']
export type RuleTime = RuleBody['time']

/** Every kind of check, in the words the rule dialog offers, grouped as the menu shows them. */
export const KINDS: { value: CheckKind; label: string; group: string }[] = [
  { value: 'has', label: 'must have', group: 'Facts it has' },
  { value: 'lacks', label: 'must not have', group: 'Facts it has' },
  { value: 'at_least', label: 'needs at least', group: 'Facts it has' },
  { value: 'at_most', label: 'can have at most', group: 'Facts it has' },
  { value: 'between', label: 'must have a number within a range for', group: 'Values' },
  { value: 'implies', label: 'if it has one fact, must also have another', group: 'Two facts' },
  { value: 'not_both', label: 'cannot have both of two facts', group: 'Two facts' },
  { value: 'believes', label: 'must believe', group: 'Beliefs' },
  { value: 'does_not_believe', label: 'must not believe', group: 'Beliefs' },
]

export const KIND_GROUPS = [...new Set(KINDS.map((kind) => kind.group))]

/** When a rule must hold, in plain words, with what that means for a story with times. */
export const TIMES: { value: RuleTime; label: string; says: string }[] = [
  {
    value: 'as_recorded',
    label: 'over every fact, whatever its time',
    says: 'Every fact counts, whenever it is true. Right for a story that does not use story time.',
  },
  {
    value: 'every_moment',
    label: 'at every moment of the story',
    says:
      'Only facts true at the same moment count together, so moving from one place to another is not being in two at once.',
  },
  {
    value: 'at_end',
    label: 'at the end of the story',
    says: 'Only facts that have not stopped being true by the end count.',
  },
]

export const emptyPattern = (predicate: string): RulePattern => ({ predicate, direction: 'out' })

/**
 * The check for a newly chosen kind, keeping what still fits: each kind has its own
 * parts, and the server refuses a check with parts its kind does not use.
 */
export function checkFor(kind: CheckKind, current: RuleCheckShape, predicate: string): RuleCheckShape {
  const pattern = current.pattern ?? emptyPattern(predicate)
  const base = { kind, count: current.count ?? 1 }
  switch (kind) {
    case 'between':
      return {
        ...base,
        pattern: { predicate: pattern.predicate, direction: 'out' },
        low: current.low ?? 0,
        high: current.high ?? null,
      }
    case 'implies':
      return { ...base, pattern, if_has: current.if_has ?? current.not_with ?? emptyPattern(predicate) }
    case 'not_both':
      return { ...base, pattern, not_with: current.not_with ?? current.if_has ?? emptyPattern(predicate) }
    case 'believes':
    case 'does_not_believe':
      return { ...base, pattern, about_id: current.about_id ?? null }
    default:
      return { ...base, pattern }
  }
}

/** A number typed into a range: empty means no limit on that side. */
export function rangeNumber(typed: string): number | null {
  if (typed.trim() === '') return null
  const value = Number(typed)
  return Number.isFinite(value) ? value : null
}

/** A select value of the form "type:Character" or "one:<entry>", as its two parts. */
export function splitChoice(choice: string): [string, string] {
  const at = choice.indexOf(':')
  return at === -1 ? [choice, ''] : [choice.slice(0, at), choice.slice(at + 1)]
}
