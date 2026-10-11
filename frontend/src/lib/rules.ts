import type { RuleBody, RulePattern } from '../api/client'

export type CheckKind = RuleBody['checks'][number]['kind']

export const KINDS: { value: CheckKind; label: string }[] = [
  { value: 'has', label: 'must have' },
  { value: 'lacks', label: 'must not have' },
  { value: 'at_least', label: 'needs at least' },
  { value: 'at_most', label: 'can have at most' },
]

export const emptyPattern = (predicate: string): RulePattern => ({ predicate, direction: 'out' })

/** A select value of the form "type:Character" or "one:<entry>", as its two parts. */
export function splitChoice(choice: string): [string, string] {
  const at = choice.indexOf(':')
  return at === -1 ? [choice, ''] : [choice.slice(0, at), choice.slice(at + 1)]
}
