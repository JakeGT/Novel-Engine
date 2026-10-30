import { useQuery } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { api, errorMessage, type MutationOperation, type VocabularyPredicate } from '../api/client'
import {
  parseRows,
  parseSchemaValues,
  schemaFields,
  splitAliases,
  toFieldText,
  toRows,
  type AttributeRow,
} from '../attributes'
import { buildObject, enteredFrom, type Entered } from '../lib/facts'
import {
  IMPORTANCE,
  IMPORTANCE_KEY,
  NEEDS_DETAIL_KEY,
  TAGS_KEY,
  detailsOf,
  marksOf,
  parseTags,
} from '../lib/marks'
import { predicateLabel, typeLabel, words } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, Field, Loading } from './ui'

/** An entry a change can be about or point to: one in the story, or one this set would add. */
export interface Candidate {
  id: string
  name: string
  type: string
}

interface Props {
  operation: MutationOperation
  candidates: Candidate[]
  busy: boolean
  onSave: (payload: Record<string, unknown>) => Promise<unknown>
  onCancel: () => void
}

type Payload = Record<string, unknown>

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const record = (value: unknown): Payload =>
  typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Payload) : {}
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

/** Shared ending of both forms: the problem, if any, then save and cancel. */
function Ending({ problem, busy, saving, onCancel }: { problem: string | null; busy: boolean; saving: boolean; onCancel: () => void }) {
  return (
    <>
      {problem && (
        <p className="notice plain" role="alert">
          {problem}
        </p>
      )}
      <p className="hint">
        The assistant’s original stays on record. An edited change counts as kept.
      </p>
      <div className="actions">
        <Button variant="primary" type="submit" disabled={busy || saving}>
          {saving ? 'Saving…' : 'Save edit'}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </>
  )
}

function useSave(onSave: Props['onSave']) {
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const save = async (payload: Payload) => {
    setSaving(true)
    setProblem(null)
    try {
      await onSave(payload)
    } catch (failure) {
      // The form stays open with what was typed.
      setProblem(errorMessage(failure))
      setSaving(false)
    }
  }
  return { problem, setProblem, saving, save }
}

/** A new entry, or changes to one: the same fields an author uses to edit an entry. */
function EntryChangeForm({ operation, busy, onSave, onCancel }: Props) {
  const { scope } = useProject()
  const creating = operation.operation_type === 'create_entity'
  const payload = operation.payload
  const before = record(operation.target_before)
  // An edit leaves out what it does not touch; the form shows the entry as it would become.
  const current = {
    name: String(payload.name ?? before.name ?? ''),
    aliases: strings(payload.aliases ?? before.aliases),
    attributes: record(payload.attributes ?? before.attributes),
  }
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const types = useMemo(() => vocabulary.data?.entity_types ?? [], [vocabulary.data])

  const [type, setType] = useState(String(payload.type ?? before.type ?? ''))
  const [name, setName] = useState(current.name)
  const [aliases, setAliases] = useState(current.aliases.join(', '))
  const [rows, setRows] = useState<AttributeRow[]>(toRows(detailsOf(current.attributes)))
  const [tags, setTags] = useState(strings(current.attributes[TAGS_KEY]).join(', '))
  const [importance, setImportance] = useState(
    typeof current.attributes[IMPORTANCE_KEY] === 'string' ? String(current.attributes[IMPORTANCE_KEY]) : '',
  )
  const [later, setLater] = useState(current.attributes[NEEDS_DETAIL_KEY] === true)
  const [typed, setTyped] = useState<Record<string, string> | null>(null)
  const [problems, setProblems] = useState<Record<string, string>>({})
  const { problem, setProblem, saving, save } = useSave(onSave)

  const fields = useMemo(
    () => schemaFields(types.find((item) => item.name === type)?.attributes_schema),
    [types, type],
  )
  const governed = fields.length > 0
  const typedValues =
    typed ??
    Object.fromEntries(fields.map((field) => [field.name, toFieldText(field, current.attributes[field.name])]))

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const parsed = governed ? parseSchemaValues(fields, typedValues) : parseRows(rows)
    const errors: Record<string, string> = { ...parsed.errors }
    if (!name.trim()) errors.name = 'Give it a name.'
    setProblems(errors)
    if (Object.keys(errors).length > 0) return

    const marks = marksOf(current.attributes)
    const tagList = parseTags(tags)
    if (tagList.length > 0) marks[TAGS_KEY] = tagList
    else delete marks[TAGS_KEY]
    if (importance) marks[IMPORTANCE_KEY] = importance
    else delete marks[IMPORTANCE_KEY]
    if (later) marks[NEEDS_DETAIL_KEY] = true
    else delete marks[NEEDS_DETAIL_KEY]
    const attributes = { ...parsed.attributes, ...marks }
    const aliasList = splitAliases(aliases)

    if (creating) {
      return void save({ ...payload, type, name: name.trim(), aliases: aliasList, attributes })
    }
    // Send only what differs from the story, so nothing else is rewritten.
    const next = {
      ...payload,
      name: name.trim() !== before.name ? name.trim() : null,
      aliases: same(aliasList, strings(before.aliases)) ? null : aliasList,
      attributes: same(attributes, record(before.attributes)) ? null : attributes,
    }
    if (next.name === null && next.aliases === null && next.attributes === null) {
      return setProblem('That would leave the entry exactly as it is now. Skip this change instead.')
    }
    void save(next)
  }

  const setRow = (index: number, patch: Partial<AttributeRow>) =>
    setRows((list) => list.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return (
    <form className="stack change-editor" onSubmit={onSubmit} noValidate aria-label={`Edit change ${operation.ordinal + 1}`}>
      <div className="grid-2">
        <Field label="Name" error={problems.name}>
          {(props) => (
            <input {...props} data-autofocus value={name} autoComplete="off" onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
        {creating && (
          <Field label="What it is">
            {(props) => (
              <select
                {...props}
                value={type}
                onChange={(event) => {
                  setType(event.target.value)
                  setTyped({})
                }}
              >
                {!types.some((item) => item.name === type) && <option value={type}>{typeLabel(type)}</option>}
                {types.map((item) => (
                  <option key={item.name} value={item.name}>
                    {typeLabel(item.name)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
      </div>
      <Field label="Also known as" hint="Nicknames, titles or other names, separated by commas.">
        {(props) => (
          <input {...props} value={aliases} autoComplete="off" onChange={(event) => setAliases(event.target.value)} />
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
                const change = (text: string) => setTyped({ ...typedValues, [field.name]: text })
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
                return <input {...props} value={value} onChange={(event) => change(event.target.value)} />
              }}
            </Field>
          ))}
        </fieldset>
      ) : (
        <fieldset className="stack tight">
          <legend>Details</legend>
          {rows.map((row, index) => (
            <div className="pair" key={index}>
              <label className="mini">
                <span>Detail</span>
                <input
                  aria-label={`Detail ${index + 1} name`}
                  value={row.key}
                  onChange={(event) => setRow(index, { key: event.target.value })}
                />
              </label>
              <label className="mini">
                <span>Value</span>
                <input
                  aria-label={`Detail ${index + 1} value`}
                  aria-invalid={problems[`row-${index}`] ? true : undefined}
                  value={row.value}
                  onChange={(event) => setRow(index, { value: event.target.value })}
                />
              </label>
              <button
                type="button"
                className="btn ghost sm"
                aria-label={`Remove detail ${index + 1}`}
                onClick={() => setRows((list) => list.filter((_, i) => i !== index))}
              >
                Remove
              </button>
              {problems[`row-${index}`] && (
                <span className="field-error" role="alert">
                  {problems[`row-${index}`]}
                </span>
              )}
            </div>
          ))}
          <div>
            <Button size="sm" icon="plus" onClick={() => setRows((list) => [...list, { key: '', value: '' }])}>
              Add a detail
            </Button>
          </div>
        </fieldset>
      )}

      <div className="grid-2">
        <Field label="Tags" hint="Short labels separated by commas: protagonist, John’s professor.">
          {(props) => (
            <input {...props} value={tags} autoComplete="off" onChange={(event) => setTags(event.target.value)} />
          )}
        </Field>
        <Field
          label="How much it matters to the story"
          hint={IMPORTANCE.find((item) => item.value === importance)?.hint}
        >
          {(props) => (
            <select {...props} value={importance} onChange={(event) => setImportance(event.target.value)}>
              <option value="">Not decided</option>
              {IMPORTANCE.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>
      <label className="check">
        <input type="checkbox" checked={later} onChange={(event) => setLater(event.target.checked)} />
        <span>Mark to fill in later</span>
      </label>

      <Ending problem={problem} busy={busy} saving={saving} onCancel={onCancel} />
    </form>
  )
}

/** A new fact or belief, edited as the sentence it reads as. */
function StatementForm({ operation, candidates, busy, onSave, onCancel }: Props) {
  const { scope } = useProject()
  const belief = operation.operation_type === 'assert_belief'
  const payload = operation.payload
  const proposition = record(payload.proposition)
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const [holderId, setHolderId] = useState(String(payload.holder_entity_id ?? ''))
  const [subjectId, setSubjectId] = useState(String(proposition.subject_id ?? ''))
  const [predicateName, setPredicateName] = useState(String(proposition.predicate ?? ''))
  const [entered, setEntered] = useState<Entered>(enteredFrom(record(proposition.object)))
  const { problem, setProblem, saving, save } = useSave(onSave)

  if (vocabulary.isPending) return <Loading>Opening the editor…</Loading>

  const subject = candidates.find((item) => item.id === subjectId)
  const predicates: VocabularyPredicate[] = (vocabulary.data?.predicates ?? []).filter(
    (item) => !subject || item.subject_types.includes(subject.type),
  )
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = candidates.filter(
    (item) =>
      item.id !== subjectId &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )
  const choice = (item: Candidate) => (
    <option key={item.id} value={item.id}>
      {item.name} ({typeLabel(item.type)})
    </option>
  )
  const change = (value: string) => setEntered({ ...entered, value })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (belief && !holderId) return setProblem('Choose who believes this.')
    if (!subject) return setProblem('Choose who or what this is about.')
    if (!predicate) return setProblem('Choose how the sentence continues.')
    const built = buildObject(predicate, entered, scope.projectId)
    if (built.error || !built.object) return setProblem(built.error ?? 'Finish the sentence first.')
    void save({
      ...payload,
      ...(belief ? { holder_entity_id: holderId } : {}),
      proposition: { ...proposition, subject_id: subjectId, predicate: predicateName, object: built.object },
    })
  }

  let value: ReactNode = null
  if (predicate?.object_shape === 'entity_ref') {
    value = (
      <Field label="Who or what">
        {(props) => (
          <select {...props} value={entered.value} onChange={(event) => change(event.target.value)}>
            <option value="">Choose…</option>
            {targets.map(choice)}
          </select>
        )}
      </Field>
    )
  } else if (predicate?.object_shape === 'boolean') {
    value = (
      <Field label="Yes or no">
        {(props) => (
          <select {...props} value={entered.value} onChange={(event) => change(event.target.value)}>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        )}
      </Field>
    )
  } else if (predicate) {
    const structured = ['enum', 'structured'].includes(predicate.object_shape)
    value = (
      <>
        <Field
          label={predicate.object_shape === 'quantity' ? 'Amount' : 'Value'}
          hint={structured ? 'This kind of value is entered as JSON.' : undefined}
        >
          {(props) => <input {...props} value={entered.value} onChange={(event) => change(event.target.value)} />}
        </Field>
        {predicate.object_shape === 'quantity' && (
          <Field label="Unit">
            {(props) => (
              <input
                {...props}
                value={entered.unit}
                onChange={(event) => setEntered({ ...entered, unit: event.target.value })}
              />
            )}
          </Field>
        )}
      </>
    )
  }

  return (
    <form className="stack change-editor" onSubmit={onSubmit} noValidate aria-label={`Edit change ${operation.ordinal + 1}`}>
      {belief && (
        <Field label="Who believes it">
          {(props) => (
            <select {...props} value={holderId} onChange={(event) => setHolderId(event.target.value)}>
              <option value="">Choose…</option>
              {candidates.filter((item) => item.type === 'Character').map(choice)}
            </select>
          )}
        </Field>
      )}
      <div className="grid-2">
        <Field label="Who or what it is about">
          {(props) => (
            <select
              {...props}
              data-autofocus
              value={subjectId}
              onChange={(event) => {
                setSubjectId(event.target.value)
                if (event.target.value === entered.value) setEntered({ value: '', unit: '' })
              }}
            >
              <option value="">Choose…</option>
              {candidates.map(choice)}
            </select>
          )}
        </Field>
        <Field label="How the sentence continues">
          {(props) => (
            <select
              {...props}
              value={predicate ? predicateName : ''}
              onChange={(event) => {
                setPredicateName(event.target.value)
                // A different kind of fact takes a different kind of value.
                setEntered({ value: '', unit: '' })
              }}
            >
              <option value="">Choose…</option>
              {predicates.map((item) => (
                <option key={item.predicate} value={item.predicate}>
                  {predicateLabel(item.predicate)}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>
      {value}
      <Ending problem={problem} busy={busy} saving={saving} onCancel={onCancel} />
    </form>
  )
}

/** Edit one suggested change with the fields an author already knows. */
export function ChangeEditor(props: Props) {
  return props.operation.operation_type.endsWith('_entity') ? (
    <EntryChangeForm {...props} />
  ) : (
    <StatementForm {...props} />
  )
}
