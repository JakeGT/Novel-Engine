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
import { COMMON_TYPES, typeLabel, typeLook, words } from '../lib/words'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { Icon } from './icons'
import { Button, Field, WriteProblem } from './ui'

interface Props {
  /** Present when editing; absent when creating. */
  entity?: CanonEntity
  /** The type preselected when creating. */
  initialType?: string
  onSaved?: (entity: CanonEntity) => void
  onCancel?: () => void
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Create or edit an entry. The type list and detail fields come from the
 * project's own vocabulary, never from a hardcoded list. Whatever the author
 * typed survives a failed save, including a clash with someone else's change.
 */
export function EntityForm({ entity, initialType, onSaved, onCancel }: Props) {
  const { scope, canWrite } = useProject()
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const types = vocabulary.data?.entity_types ?? []

  const [type, setType] = useState(entity?.type ?? initialType ?? 'Character')
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
  // Seed typed fields from the entry once the vocabulary tells us its shape.
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
    if (!trimmedName) errors.name = 'Give it a name.'
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

  const everyType = types.length > 0 ? types : [{ name: type, core: true, attributes_schema: null }]
  const [showAllTypes, setShowAllTypes] = useState(false)
  // Lead with the usual types and the project's own; the long tail is one click away.
  const usual = everyType.filter(
    (item) => COMMON_TYPES.includes(item.name) || !item.core || item.name === type,
  )
  const choices = showAllTypes || usual.length === 0 ? everyType : usual

  return (
    <form
      className="stack"
      onSubmit={onSubmit}
      noValidate
      aria-label={entity ? `Edit ${entity.name}` : 'New entry'}
    >
      {!entity && (
        <fieldset>
          <legend>What are you adding?</legend>
          <div className="type-picker">
            {choices.map((item) => {
              const look = typeLook(item.name)
              return (
                <label key={item.name} className="type-option">
                  <input
                    type="radio"
                    name="entry-type"
                    value={item.name}
                    checked={type === item.name}
                    onChange={() => {
                      setType(item.name)
                      setTyped({})
                      setTouchedTyped(true)
                    }}
                  />
                  <span className={`avatar sm tint-${look.tint}`} aria-hidden="true">
                    <Icon name={look.icon} size={15} />
                  </span>
                  {typeLabel(item.name)}
                </label>
              )
            })}
          </div>
          {choices.length < everyType.length && (
            <div style={{ marginTop: '0.6rem' }}>
              <Button size="sm" variant="ghost" onClick={() => setShowAllTypes(true)}>
                Show all {everyType.length} types
              </Button>
            </div>
          )}
        </fieldset>
      )}

      <Field label="Name" error={problems.name}>
        {(props) => (
          <input
            {...props}
            data-autofocus
            value={name}
            autoComplete="off"
            placeholder={entity ? undefined : 'Mara Venn'}
            onChange={(event) => setName(event.target.value)}
            required
          />
        )}
      </Field>
      {twins.length > 0 && (
        <p className="notice plain" role="status">
          You already have a {typeLabel(type).toLowerCase()} called “{twins[0].name}”. Two entries
          can share a name, so you can still save this — just check it is not a duplicate.
        </p>
      )}

      <Field label="Also known as" hint="Nicknames, titles or other names, separated by commas. Optional.">
        {(props) => (
          <input
            {...props}
            value={aliases}
            autoComplete="off"
            onChange={(event) => setAliases(event.target.value)}
          />
        )}
      </Field>

      {governed ? (
        <fieldset className="stack">
          <legend>{typeLabel(type)} details</legend>
          {fields.map((field) => (
            <Field
              key={field.name}
              label={field.required ? `${words(field.name)} (required)` : words(field.name)}
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
                          {field.kind === 'boolean' ? (option === 'true' ? 'Yes' : 'No') : option}
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
        <fieldset className="stack tight">
          <legend>Details</legend>
          <p className="hint small muted">
            Lasting traits, like “role: harbor pilot” or “eye colour: grey”. Optional — add them
            whenever you know them.
          </p>
          {rows.map((row, index) => (
            <div className="pair" key={index}>
              <input
                aria-label={`Detail ${index + 1} name`}
                placeholder="role"
                value={row.key}
                onChange={(event) => setRow(index, { key: event.target.value })}
              />
              <input
                aria-label={`Detail ${index + 1} value`}
                aria-invalid={problems[`row-${index}`] ? true : undefined}
                placeholder="harbor pilot"
                value={row.value}
                onChange={(event) => setRow(index, { value: event.target.value })}
              />
              <button
                type="button"
                className="btn ghost icon"
                aria-label={`Remove detail ${index + 1}`}
                onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
              >
                <Icon name="x" />
              </button>
              {problems[`row-${index}`] && (
                <span className="field-error" role="alert">
                  {problems[`row-${index}`]}
                </span>
              )}
            </div>
          ))}
          <div>
            <Button
              size="sm"
              icon="plus"
              onClick={() => setRows((current) => [...current, { key: '', value: '' }])}
            >
              Add a detail
            </Button>
          </div>
        </fieldset>
      )}

      <details className="more" open={Boolean(problems.slug) || undefined}>
        <summary>More options</summary>
        <Field
          label="Short name for links"
          hint={
            entity
              ? 'Lowercase letters, digits and hyphens. Must be unique among your current entries.'
              : 'Optional. Leave empty and one is made from the name.'
          }
          error={problems.slug}
        >
          {(props) => (
            <input {...props} value={slug} autoComplete="off" onChange={(event) => setSlug(event.target.value)} />
          )}
        </Field>
      </details>

      {problems.form && (
        <p className="notice plain" role="alert">
          {problems.form}
        </p>
      )}
      <WriteProblem write={write} scope={scope} />
      <div className="actions end">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button variant="primary" type="submit" disabled={write.pending || !canWrite}>
          {write.pending ? 'Saving…' : entity ? 'Save changes' : 'Add to story bible'}
        </Button>
      </div>
    </form>
  )
}
