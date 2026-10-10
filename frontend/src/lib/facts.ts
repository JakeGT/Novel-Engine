// Turning what an author types into a typed fact value, and back.
import type { VocabularyPredicate } from '../api/client'

export interface Entered {
  value: string
  unit: string
}

export function buildObject(
  predicate: VocabularyPredicate,
  entered: Entered,
  projectId: string,
): { object?: Record<string, unknown>; error?: string } {
  const text = entered.value.trim()
  if (!text) return { error: 'Finish the sentence first.' }
  switch (predicate.object_shape) {
    case 'entity_ref':
      return { object: { kind: 'entity_ref', entity_id: text, project_id: projectId } }
    case 'string':
      return { object: { kind: 'string', value: text } }
    case 'integer':
      return /^-?\d+$/.test(text)
        ? { object: { kind: 'integer', value: Number(text) } }
        : { error: 'Enter a whole number.' }
    case 'decimal':
      return Number.isNaN(Number(text))
        ? { error: 'Enter a number.' }
        : { object: { kind: 'decimal', value: text } }
    case 'boolean':
      return { object: { kind: 'boolean', value: text === 'true' } }
    case 'quantity':
      if (Number.isNaN(Number(text))) return { error: 'Enter a number for the amount.' }
      if (!entered.unit.trim()) return { error: 'Add a unit, such as km or years.' }
      return { object: { kind: 'quantity', value: text, unit: entered.unit.trim() } }
    default:
      // Lists of allowed values and structured values carry extra fields; enter them as JSON.
      try {
        return { object: { kind: predicate.object_shape, ...(JSON.parse(text) as object) } }
      } catch {
        return { error: 'Enter this value as JSON.' }
      }
  }
}

/** The text an author would have typed to produce this value: the reverse of buildObject. */
export function enteredFrom(object: Record<string, unknown> | null | undefined): Entered {
  if (!object) return { value: '', unit: '' }
  switch (object.kind) {
    case 'entity_ref':
      return { value: String(object.entity_id ?? ''), unit: '' }
    case 'string':
    case 'integer':
    case 'decimal':
    case 'boolean':
      return { value: String(object.value ?? ''), unit: '' }
    case 'quantity':
      return { value: String(object.value ?? ''), unit: String(object.unit ?? '') }
    default: {
      const rest = Object.fromEntries(Object.entries(object).filter(([key]) => key !== 'kind'))
      return { value: JSON.stringify(rest), unit: '' }
    }
  }
}

const FORMS = new Set(['create_entity', 'update_entity', 'assert_fact', 'assert_belief'])

/** Whether a suggested change of this kind can be edited with a form rather than as stored. */
export function hasChangeForm(operationType: string): boolean {
  return FORMS.has(operationType)
}

/** Shown under a value that has to be typed as JSON. */
export const JSON_HINTS: Record<string, string> = {
  enum: 'Enter as JSON, for example {"value": "wary", "allowed": ["wary", "open"]}',
  structured: 'Enter as JSON, for example {"value": {"note": "…"}}',
}
