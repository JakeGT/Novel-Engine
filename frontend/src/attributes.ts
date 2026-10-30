// Helpers for turning schema-governed attribute definitions into form state and back.

export interface AttributeRow {
  key: string
  value: string
}

type JsonSchema = Record<string, unknown>

export interface SchemaField {
  name: string
  label: string
  required: boolean
  kind: 'string' | 'integer' | 'number' | 'boolean' | 'enum' | 'json'
  options: string[]
  maxLength?: number
  minimum?: number
  maximum?: number
}

function resolve(schema: JsonSchema, root: JsonSchema): JsonSchema {
  let current = schema
  // Optional fields are emitted as anyOf [X, null]; references point into $defs.
  for (let depth = 0; depth < 5; depth += 1) {
    const ref = current.$ref
    if (typeof ref === 'string') {
      const name = ref.split('/').pop() ?? ''
      current = ((root.$defs as Record<string, JsonSchema> | undefined)?.[name] ?? {}) as JsonSchema
      continue
    }
    const anyOf = current.anyOf as JsonSchema[] | undefined
    if (Array.isArray(anyOf)) {
      const real = anyOf.filter((option) => option.type !== 'null')
      if (real.length === 1) {
        current = { ...real[0] }
        continue
      }
    }
    break
  }
  return current
}

/** Flatten a published entity type's attribute JSON Schema into renderable fields. */
export function schemaFields(schema: JsonSchema | null | undefined): SchemaField[] {
  if (!schema) return []
  const properties = (schema.properties ?? {}) as Record<string, JsonSchema>
  const required = new Set((schema.required as string[] | undefined) ?? [])
  return Object.entries(properties).map(([name, raw]) => {
    const field = resolve(raw, schema)
    const options = Array.isArray(field.enum) ? (field.enum as unknown[]).map(String) : []
    let kind: SchemaField['kind'] = 'json'
    if (options.length > 0) kind = 'enum'
    else if (field.type === 'string') kind = 'string'
    else if (field.type === 'integer') kind = 'integer'
    else if (field.type === 'number') kind = 'number'
    else if (field.type === 'boolean') kind = 'boolean'
    return {
      name,
      label: name.replace(/_/g, ' '),
      required: required.has(name),
      kind,
      options,
      maxLength: typeof field.maxLength === 'number' ? field.maxLength : undefined,
      minimum: typeof field.minimum === 'number' ? field.minimum : undefined,
      maximum: typeof field.maximum === 'number' ? field.maximum : undefined,
    }
  })
}

export function toFieldText(field: SchemaField, value: unknown): string {
  if (value === undefined || value === null) return ''
  if (field.kind === 'json') return JSON.stringify(value)
  return String(value)
}

export interface ParsedAttributes {
  attributes: Record<string, unknown>
  errors: Record<string, string>
}

/** Validate and type the entered values against the governed fields. */
export function parseSchemaValues(
  fields: SchemaField[],
  values: Record<string, string>,
): ParsedAttributes {
  const attributes: Record<string, unknown> = {}
  const errors: Record<string, string> = {}
  for (const field of fields) {
    const text = (values[field.name] ?? '').trim()
    if (text === '') {
      if (field.required) errors[field.name] = `${field.label} is required.`
      continue
    }
    if (field.kind === 'integer') {
      if (!/^-?\d+$/.test(text)) errors[field.name] = `${field.label} must be a whole number.`
      else attributes[field.name] = Number(text)
    } else if (field.kind === 'number') {
      if (Number.isNaN(Number(text))) errors[field.name] = `${field.label} must be a number.`
      else attributes[field.name] = Number(text)
    } else if (field.kind === 'boolean') {
      attributes[field.name] = text === 'true'
    } else if (field.kind === 'enum') {
      if (!field.options.includes(text)) errors[field.name] = `${field.label} is not an allowed value.`
      else attributes[field.name] = text
    } else if (field.kind === 'json') {
      try {
        attributes[field.name] = JSON.parse(text)
      } catch {
        errors[field.name] = `${field.label} must be valid JSON.`
      }
    } else {
      if (field.maxLength !== undefined && text.length > field.maxLength) {
        errors[field.name] = `${field.label} must be at most ${field.maxLength} characters.`
      } else attributes[field.name] = text
    }
    const numeric = attributes[field.name]
    if (typeof numeric === 'number') {
      if (field.minimum !== undefined && numeric < field.minimum) {
        errors[field.name] = `${field.label} must be at least ${field.minimum}.`
      }
      if (field.maximum !== undefined && numeric > field.maximum) {
        errors[field.name] = `${field.label} must be at most ${field.maximum}.`
      }
    }
  }
  return { attributes, errors }
}

export function toRows(attributes: Record<string, unknown> | undefined): AttributeRow[] {
  return Object.entries(attributes ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
}

/**
 * Free-form attributes: numbers, true/false, and JSON values keep their type;
 * everything else is stored as text.
 */
export function parseRows(rows: AttributeRow[]): ParsedAttributes {
  const attributes: Record<string, unknown> = {}
  const errors: Record<string, string> = {}
  rows.forEach((row, index) => {
    const key = row.key.trim()
    if (!key && !row.value.trim()) return
    if (!key) {
      errors[`row-${index}`] = 'Every attribute needs a name.'
      return
    }
    if (key in attributes) {
      errors[`row-${index}`] = `Attribute "${key}" is listed twice.`
      return
    }
    const text = row.value.trim()
    if (/^(-?\d+(\.\d+)?|true|false|null)$/.test(text) || /^[[{]/.test(text)) {
      try {
        attributes[key] = JSON.parse(text)
        return
      } catch {
        // Not valid JSON after all; keep it as text.
      }
    }
    attributes[key] = row.value
  })
  return { attributes, errors }
}

export function splitAliases(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((alias) => alias.trim())
    .filter(Boolean)
}

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

export function slugProblem(slug: string): string | null {
  if (!slug) return null
  if (slug.length > 100) return 'Slug must be at most 100 characters.'
  return SLUG.test(slug) ? null : 'Use lowercase letters, digits, and single hyphens.'
}
