// One place that turns the engine's vocabulary into words an author would use.
// The server speaks in types, predicates, operations and revisions; the screen
// speaks in entries, facts, changes and versions.

import type { IconName } from '../components/icons'

export type Tint = 'violet' | 'teal' | 'amber' | 'rose' | 'blue' | 'green' | 'slate'

interface TypeLook {
  plural: string
  icon: IconName
  tint: Tint
  blurb: string
}

const TYPE_LOOKS: Record<string, TypeLook> = {
  Character: { plural: 'Characters', icon: 'user', tint: 'violet', blurb: 'People and creatures' },
  Location: { plural: 'Locations', icon: 'pin', tint: 'teal', blurb: 'Places in your world' },
  Object: { plural: 'Objects', icon: 'cube', tint: 'amber', blurb: 'Things that matter' },
  Faction: { plural: 'Factions', icon: 'flag', tint: 'rose', blurb: 'Groups and allegiances' },
  Event: { plural: 'Events', icon: 'spark', tint: 'blue', blurb: 'Things that happened' },
  Concept: { plural: 'Concepts', icon: 'bulb', tint: 'green', blurb: 'Ideas and lore' },
  System: { plural: 'Systems', icon: 'orbit', tint: 'slate', blurb: 'Magic, tech, rules' },
  Conflict: { plural: 'Conflicts', icon: 'shield', tint: 'rose', blurb: 'Struggles and stakes' },
  Constraint: { plural: 'Constraints', icon: 'layers', tint: 'slate', blurb: 'Limits that must hold' },
  Goal: { plural: 'Goals', icon: 'check', tint: 'green', blurb: 'What someone wants' },
  Law: { plural: 'Laws', icon: 'book', tint: 'amber', blurb: 'Rules of the world' },
  Pressure: { plural: 'Pressures', icon: 'clock', tint: 'rose', blurb: 'Forces pushing on the story' },
  Resource: { plural: 'Resources', icon: 'key', tint: 'teal', blurb: 'Things in limited supply' },
  Secret: { plural: 'Secrets', icon: 'eye', tint: 'violet', blurb: 'What is hidden, and from whom' },
  Theme: { plural: 'Themes', icon: 'feather', tint: 'blue', blurb: 'Ideas the story explores' },
  World: { plural: 'Worlds', icon: 'orbit', tint: 'teal', blurb: 'The setting as a whole' },
  WorldState: { plural: 'World states', icon: 'layers', tint: 'blue', blurb: 'How things stand at a moment' },
}

/** The types most stories start with; the rest stay one click away. */
export const COMMON_TYPES = ['Character', 'Location', 'Faction', 'Object', 'Event']

const FALLBACK_TINTS: Tint[] = ['blue', 'rose', 'green', 'amber', 'teal', 'violet', 'slate']

function hash(text: string): number {
  let value = 0
  for (const char of text) value = (value * 31 + char.charCodeAt(0)) >>> 0
  return value
}

/** "ShipClass" or "ship_class" → "Ship class". */
export function words(identifier: string): string {
  const spaced = identifier
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim()
    .toLowerCase()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function pluralize(noun: string): string {
  if (/[^aeiou]y$/i.test(noun)) return `${noun.slice(0, -1)}ies`
  if (/(s|x|z|ch|sh)$/i.test(noun)) return `${noun}es`
  return `${noun}s`
}

export function typeLabel(type: string): string {
  return words(type)
}

export function typePlural(type: string): string {
  return TYPE_LOOKS[type]?.plural ?? pluralize(words(type))
}

export function typeLook(type: string): TypeLook {
  return (
    TYPE_LOOKS[type] ?? {
      plural: typePlural(type),
      icon: 'shapes',
      tint: FALLBACK_TINTS[hash(type) % FALLBACK_TINTS.length],
      blurb: 'A type you added',
    }
  )
}

const PREDICATES: Record<string, string> = {
  LOCATED_AT: 'is located at',
  OWNS: 'owns',
  MEMBER_OF: 'is a member of',
  PARTICIPATED_IN: 'took part in',
  KNOWS: 'knows',
  RELATED_TO: 'is related to',
  CAUSED: 'caused',
  HAS_NAME: 'is named',
  HAS_COLOR: 'has the colour',
  HAS_POPULATION: 'has a population of',
  HAS_AREA: 'has an area of',
  IS_HAUNTED: 'is haunted',
  HAS_ALIGNMENT: 'has the alignment',
  HAS_STATUS: 'has the status',
  HAS_DETAIL: 'has the detail',
}

/** A predicate as the verb of a sentence: KNOWS → "knows", TRAINED_BY → "trained by". */
export function predicateLabel(predicate: string): string {
  return PREDICATES[predicate] ?? predicate.replace(/_/g, ' ').toLowerCase()
}

type Names = Map<string, string> | ((id: string) => string | undefined)

function nameOf(names: Names, id: string): string {
  const found = typeof names === 'function' ? names(id) : names.get(id)
  return found ?? 'an entry that is not loaded'
}

/** The object of a fact or belief as plain text. */
export function objectText(object: Record<string, unknown> | undefined, names: Names): string {
  if (!object) return ''
  switch (object.kind) {
    case 'entity_ref':
      return nameOf(names, String(object.entity_id))
    case 'boolean':
      return object.value ? 'yes' : 'no'
    case 'quantity':
      return `${String(object.value)} ${String(object.unit ?? '')}`.trim()
    case 'structured':
      return JSON.stringify(object.value)
    default:
      return String(object.value ?? '')
  }
}

/** A stored value shown to a person: no JSON punctuation for the simple cases. */
export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) {
    return value.length === 0 ? '—' : value.map((item) => valueText(item)).join(', ')
  }
  const pairs = Object.entries(value as Record<string, unknown>)
  if (pairs.length === 0) return '—'
  // A flat set of details reads as "role: pilot, age: 34"; anything deeper stays as data.
  return pairs.every(([, item]) => item === null || typeof item !== 'object')
    ? pairs.map(([key, item]) => `${words(key).toLowerCase()}: ${valueText(item)}`).join(', ')
    : JSON.stringify(value)
}

// The story plan: what each part is called, and how an entry takes part in one.
const PLAN_KINDS: Record<string, { one: string; many: string }> = {
  story: { one: 'Story', many: 'Stories' },
  arc: { one: 'Arc', many: 'Arcs' },
  beat: { one: 'Beat', many: 'Beats' },
  scene: { one: 'Scene', many: 'Scenes' },
  thread: { one: 'Thread', many: 'Threads' },
}

export function planKindLabel(kind: string): string {
  return PLAN_KINDS[kind]?.one ?? words(kind)
}

export function planKindPlural(kind: string): string {
  return PLAN_KINDS[kind]?.many ?? `${words(kind)}s`
}

export const PLAN_ROLES: Record<string, string> = {
  point_of_view: 'point of view',
  place: 'place',
  participant: 'in it',
  about: 'whose it is',
}

export const ORIGINS: Record<string, string> = {
  author: 'an author',
  model: 'the AI assistant',
  import: 'an import',
  migration: 'the system',
  machine: 'a connected assistant',
  // Worked out by the story's own rules: who believes what they were told or saw (KNW-006).
  derived: 'what people were told or saw',
}

export function originLabel(origin: string | null | undefined): string {
  if (!origin) return 'an unknown source'
  return ORIGINS[origin] ?? origin
}

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'accent'

export const CHANGE_STATES: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Waiting for review', tone: 'warn' },
  validating: { label: 'Waiting for review', tone: 'warn' },
  needs_review: { label: 'Waiting for review', tone: 'warn' },
  approved: { label: 'Approved, not in canon yet', tone: 'info' },
  committing: { label: 'Needs attention', tone: 'danger' },
  recovery_required: { label: 'Needs attention', tone: 'danger' },
  committed: { label: 'In your story', tone: 'ok' },
  rejected: { label: 'Discarded', tone: 'danger' },
}

const OPEN_STATES = new Set(['draft', 'validating', 'needs_review', 'approved'])

/** Still undecided: neither added to canon nor discarded. */
export function isOpenChange(status: string): boolean {
  return OPEN_STATES.has(status)
}

export const REVIEW_REASONS: Record<string, string> = {
  protected_origin: 'it did not come directly from an author',
  destructive_operation: 'it archives something',
  bulk_change: 'it changes many things at once',
  conflict_resolution: 'it undoes earlier history',
}

export interface OperationLike {
  operation_type: string
  payload: Record<string, unknown>
  target_before?: Record<string, unknown> | null
}

function proposition(payload: Record<string, unknown>): Record<string, unknown> {
  return (payload.proposition ?? {}) as Record<string, unknown>
}

function sentence(payload: Record<string, unknown>, names: Names): string {
  const p = proposition(payload)
  const subject = nameOf(names, String(p.subject_id ?? ''))
  const object = objectText(p.object as Record<string, unknown> | undefined, names)
  return `${subject} ${predicateLabel(String(p.predicate ?? ''))} ${object}`.trim()
}

function planTarget(operation: OperationLike, names: Names): string {
  const before = operation.target_before ?? {}
  return String(before.title ?? '') || nameOf(names, String(operation.payload.id ?? ''))
}

/** One line describing what a proposed change does, in the author's terms. */
export function describeOperation(operation: OperationLike, names: Names): string {
  const { payload } = operation
  const before = operation.target_before ?? {}
  const target = () =>
    String(before.name ?? '') || nameOf(names, String(payload.id ?? '')) || 'an entry'
  switch (operation.operation_type) {
    case 'create_entity':
      return `Add ${typeLabel(String(payload.type ?? 'entry')).toLowerCase()} “${String(payload.name ?? '')}”`
    case 'update_entity':
      return `Update “${target()}”`
    case 'retire_entity':
      return payload.merged_into
        ? `Archive “${target()}”, merged into “${nameOf(names, String(payload.merged_into))}”`
        : `Archive “${target()}”`
    case 'restore_entity':
      return `Bring back “${target()}”`
    case 'merge_entities':
      return `Merge “${target()}” into “${nameOf(names, String(payload.into ?? ''))}”`
    case 'assert_fact':
      return `Add fact: ${sentence(payload, names)}`
    case 'close_fact':
      // Say which fact: the record as it stands now holds its sentence.
      return before.proposition ? `End a fact: ${sentence(before, names)}` : 'End a fact'
    case 'amend_fact': {
      const parts: string[] = []
      if (payload.starts || payload.ends) parts.push('say when a fact is true')
      if (payload.visibility === 'secret') parts.push('make a fact a secret')
      if (payload.visibility === 'common') parts.push('mark a fact as known to everyone')
      if (payload.visibility === 'ordinary') parts.push('stop treating a fact as a secret')
      const text = parts.join(' and ') || 'change when a fact is true'
      const which = before.proposition ? `: ${sentence(before, names)}` : ''
      return text.charAt(0).toUpperCase() + text.slice(1) + which
    }
    case 'assert_belief':
      return `${nameOf(names, String(payload.holder_entity_id ?? ''))} believes: ${sentence(payload, names)}`
    case 'close_belief':
      return before.proposition
        ? `End a belief: ${nameOf(names, String(before.holder_entity_id ?? ''))} believes ${sentence(before, names)}`
        : 'End a belief'
    case 'create_event':
      return `Add event “${String(payload.name ?? '')}”`
    case 'amend_event':
      return `Update event “${target()}”`
    case 'retire_event':
      return `Archive event “${target()}”`
    case 'create_plan_item':
      return `Plan ${planKindLabel(String(payload.kind ?? '')).toLowerCase()} “${String(payload.title ?? '')}”`
    case 'update_plan_item':
      return `Change the plan: “${planTarget(operation, names)}”`
    case 'retire_plan_item':
      return `Remove from the plan: “${planTarget(operation, names)}”`
    default:
      return words(operation.operation_type)
  }
}

/** Names introduced by the operations themselves, so a new entry can be named before it exists. */
export function namesFromOperations(operations: OperationLike[]): Map<string, string> {
  const names = new Map<string, string>()
  for (const operation of operations) {
    const { payload } = operation
    if (typeof payload.id === 'string' && typeof payload.name === 'string') {
      names.set(payload.id, payload.name)
    }
    // A part of the story plan is known by its title.
    if (typeof payload.id === 'string' && typeof payload.title === 'string') {
      names.set(payload.id, payload.title)
    }
    const before = operation.target_before
    if (before && typeof payload.id === 'string' && typeof before.name === 'string') {
      names.set(payload.id, before.name)
    }
  }
  return names
}

const ROLE_LABELS: Record<string, string> = { owner: 'Owner', editor: 'Editor', viewer: 'Reader' }

/** A member's role, named the same on every screen. */
export function roleLabel(role: string | undefined): string {
  return role ? (ROLE_LABELS[role] ?? role) : ''
}

export function initial(name: string): string {
  const trimmed = name.trim()
  return trimmed ? Array.from(trimmed)[0].toUpperCase() : '?'
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

export function changeSetTitle(change: { intent: string; origin: string }): string {
  if (change.intent === 'revert') return 'Restore an earlier version'
  return `Changes from ${originLabel(change.origin)}`
}
