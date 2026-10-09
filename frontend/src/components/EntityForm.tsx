import { useQuery } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { api, type CanonEntity, type UpdateEntityRequest } from '../api/client'
import {
  parseRows,
  parseSchemaValues,
  schemaFields,
  slugProblem,
  splitAliases,
  toFieldText,
  toRows,
  type AttributeRow,
} from '../attributes'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { Field, WriteProblem } from './ui'

interface Props {
  /** Present when editing; absent when creating. */
  entity?: CanonEntity
  onSaved?: (entity: CanonEntity) => void
  onCancel?: () => void
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Create or edit an entity. The type list and attribute fields come from the
 * project's governed vocabulary, never from a hardcoded list. Whatever the author
 * typed survives a failed save, including a stale-head conflict.
 */
export function EntityForm({ entity, onSaved, onCancel }: Props) {
  const { scope, canWrite } = useProject()
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const types = vocabulary.data?.entity_types ?? []

  const [type, setType] = useState(entity?.type ?? 'Character')
  const [name, setName] = useState(entity?.name ?? '')
  const [slug, setSlug] = useState(entity?.slug ?? '')
  const [aliases, setAliases] = useState((entity?.aliases ?? []).join(', '))
  const [rows, setRows] = useState<AttributeRow[]>(toRows(entity?.attributes))
  const [typed, setTyped] = useState<Record<string, string>>({})
  const [touchedTyped, setTouchedTyped] = useState(false)
  const [problems, setProblems] = useState<Record<string, string>>({})

  const typeSpec = types.find((item) => item.name === type)
  const fields = useMemo(() => schemaFields(typeSpec?.attributes_schema), [typeSpec])
  const governed = fields.length > 0
  // Seed typed fields from the entity once the vocabulary tells us its shape.
  const typedValues = useMemo(() => {
    if (touchedTyped || !entity) return typed
    return Object.fromEntries(
      fields.map((field) => [field.name, toFieldText(field, entity.attributes?.[field.name])]),
    )
  }, [touchedTyped, typed, entity, fields])

  const trimmedName = name.trim()
  const collisions = useQuery({
    queryKey: scopedKey(scope, 'name-collisions', type, trimmedName),
    queryFn: () => api.nameCollisions(scope, type, trimmedName),
    enabled: trimmedName.length >= 2,
  })
  const twins = (collisions.data?.items ?? []).filter((twin) => twin.id !== entity?.id)

  const collect = () => {
    const errors: Record<string, string> = {}
    if (!trimmedName) errors.name = 'Name is required.'
    const slugError = slugProblem(slug.trim())
    if (slugError) errors.slug = slugError
    const parsed = governed ? parseSchemaValues(fields, typedValues) : parseRows(rows)
    Object.assign(errors, parsed.errors)
    return { errors, attributes: parsed.attributes }
  }

  const buildUpdate = (expectedRevision: number): UpdateEntityRequest => {
    // Send only what changed, so an unrelated edit never rewrites other fields.
    const { attributes } = collect()
    const aliasList = splitAliases(aliases)
    const body: UpdateEntityRequest = { expected_revision: expectedRevision }
    if (!entity) return body
    if (trimmedName !== entity.name) body.name = trimmedName
    if (slug.trim() && slug.trim() !== (entity.slug ?? '')) body.slug = slug.trim()
    if (!same(aliasList, entity.aliases ?? [])) body.aliases = aliasList
    if (!same(attributes, entity.attributes ?? {})) body.attributes = attributes
    return body
  }

  const write = useCanonWrite<CanonEntity>(
    (expectedRevision) => {
      if (entity) return api.updateEntity(scope, entity.id, buildUpdate(expectedRevision))
      return api.createEntity(scope, {
        type,
        name: trimmedName,
        slug: slug.trim() || null,
        aliases: splitAliases(aliases),
        attributes: collect().attributes,
        expected_revision: expectedRevision,
      })
    },
    (saved) => {
      if (!entity) {
        setName('')
        setSlug('')
        setAliases('')
        setRows([])
        setTyped({})
        setTouchedTyped(false)
      }
      onSaved?.(saved)
    },
  )

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const { errors } = collect()
    if (entity && Object.keys(errors).length === 0 && Object.keys(buildUpdate(0)).length === 1) {
      errors.form = 'Nothing has changed, so there is nothing to save.'
    }
    setProblems(errors)
    if (Object.keys(errors).length === 0) void write.submit()
  }

  const setRow = (index: number, patch: Partial<AttributeRow>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={entity ? 'Edit entity' : 'Create entity'}>
      <Field label="Type" hint={entity ? 'An entity keeps the type it was created with.' : undefined}>
        {(props) => (
          <select
            {...props}
            value={type}
            disabled={Boolean(entity)}
            onChange={(event) => {
              setType(event.target.value)
              setTyped({})
              setTouchedTyped(true)
            }}
          >
            {types.length === 0 && <option value={type}>{type}</option>}
            {types.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
                {item.core ? '' : ' (project type)'}
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field label="Name" error={problems.name}>
        {(props) => (
          <input {...props} value={name} onChange={(event) => setName(event.target.value)} required />
        )}
      </Field>
      {twins.length > 0 && (
        <p className="notice" role="status">
          Another active {type} is already named “{twins[0].name}”. Names are not identity, so you
          can still save this one; check it is not a duplicate first.
        </p>
      )}

      <Field
        label="Slug"
        hint={entity ? 'A unique, URL-safe handle.' : 'Optional. Leave empty to derive one from the name.'}
        error={problems.slug}
      >
        {(props) => <input {...props} value={slug} onChange={(event) => setSlug(event.target.value)} />}
      </Field>

      <Field label="Aliases" hint="Other names this entity is known by, separated by commas.">
        {(props) => (
          <input {...props} value={aliases} onChange={(event) => setAliases(event.target.value)} />
        )}
      </Field>

      {governed ? (
        <fieldset>
          <legend>{type} attributes</legend>
          {fields.map((field) => (
            <Field
              key={field.name}
              label={field.required ? `${field.label} (required)` : field.label}
              error={problems[field.name]}
            >
              {(props) => {
                const value = typedValues[field.name] ?? ''
                const change = (next: string) => {
                  setTyped({ ...typedValues, [field.name]: next })
                  setTouchedTyped(true)
                }
                if (field.kind === 'enum' || field.kind === 'boolean') {
                  const options = field.kind === 'boolean' ? ['true', 'false'] : field.options
                  return (
                    <select {...props} value={value} onChange={(event) => change(event.target.value)}>
                      <option value="">{field.required ? 'Choose…' : 'Not set'}</option>
                      {options.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  )
                }
                return (
                  <input
                    {...props}
                    value={value}
                    inputMode={field.kind === 'integer' || field.kind === 'number' ? 'decimal' : undefined}
                    onChange={(event) => change(event.target.value)}
                  />
                )
              }}
            </Field>
          ))}
        </fieldset>
      ) : (
        <fieldset>
          <legend>Attributes</legend>
          <p className="muted">
            Stable traits only. Numbers and true/false keep their type; everything else is text.
          </p>
          {rows.map((row, index) => (
            <div className="pair" key={index}>
              <input
                aria-label={`Attribute ${index + 1} name`}
                value={row.key}
                onChange={(event) => setRow(index, { key: event.target.value })}
              />
              <input
                aria-label={`Attribute ${index + 1} value`}
                aria-invalid={problems[`row-${index}`] ? true : undefined}
                value={row.value}
                onChange={(event) => setRow(index, { value: event.target.value })}
              />
              <button
                type="button"
                className="button secondary"
                aria-label={`Remove attribute ${index + 1}`}
                onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
              >
                Remove
              </button>
              {problems[`row-${index}`] && (
                <span className="error" role="alert">
                  {problems[`row-${index}`]}
                </span>
              )}
            </div>
          ))}
          <button
            type="button"
            className="button secondary"
            onClick={() => setRows((current) => [...current, { key: '', value: '' }])}
          >
            Add attribute
          </button>
        </fieldset>
      )}

      <div className="actions">
        <button className="button" type="submit" disabled={write.pending || !canWrite}>
          {entity ? 'Save changes' : 'Create entity'}
        </button>
        {onCancel && (
          <button type="button" className="button secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {problems.form && (
        <p className="notice" role="alert">
          {problems.form}
        </p>
      )}
      <WriteProblem write={write} scope={scope} />
    </form>
  )
}
