import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import {
  api,
  errorMessage,
  isApiError,
  type SchemaValidationReport,
  type SchemaVersion,
} from '../api/client'
import { schemaFields } from '../attributes'
import { Icon } from '../components/icons'
import {
  ConfirmDialog,
  Badge,
  Button,
  Dialog,
  ErrorNotice,
  Field,
  Json,
  Loading,
  ReadOnlyNotice,
  TypeAvatar,
} from '../components/ui'
import { predicateLabel, typeLabel, typeLook, words, type Tone } from '../lib/words'
import { fieldIdentifier, predicateIdentifier, typeIdentifier } from '../lib/identifiers'
import { scopedKey, useInvalidateProject, useProject } from '../project'

type Definition = Record<string, unknown>

const EMPTY: Definition = {
  label: 'Project schema',
  description: '',
  entity_extensions: [],
  predicate_extensions: [],
}

const COMPATIBILITY: Record<string, { label: string; tone: Tone }> = {
  additive: { label: 'Safe — everything you already have stays valid', tone: 'ok' },
  migration_required: { label: 'Existing entries would need updating first', tone: 'warn' },
  forbidden: { label: 'Not allowed — this would break existing entries', tone: 'danger' },
  invalid: { label: 'Not valid', tone: 'danger' },
}

const FIELD_KINDS: { value: string; label: string }[] = [
  { value: 'string', label: 'Text' },
  { value: 'integer', label: 'Whole number' },
  { value: 'decimal', label: 'Number' },
  { value: 'boolean', label: 'Yes or no' },
  { value: 'enum', label: 'One of a list' },
]

const KIND_WORDS: Record<string, string> = {
  string: 'text',
  integer: 'whole number',
  number: 'number',
  decimal: 'number',
  boolean: 'yes or no',
  enum: 'one of a list',
  quantity: 'an amount with a unit',
  structured: 'a structured value',
  json: 'a structured value',
}

const OBJECT_KINDS: { value: string; label: string }[] = [
  { value: 'entity_ref', label: 'Another entry' },
  { value: 'string', label: 'Some text' },
  { value: 'integer', label: 'A whole number' },
  { value: 'decimal', label: 'A number' },
  { value: 'boolean', label: 'Yes or no' },
]

interface FieldRow {
  label: string
  kind: string
  values: string
  required: boolean
}

function list(definition: Definition, key: string): Record<string, unknown>[] {
  const value = definition[key]
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : []
}

function parseDefinition(text: string): { definition?: Definition; error?: string } {
  try {
    const value: unknown = JSON.parse(text)
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return { error: 'The definition must be a JSON object.' }
    }
    return { definition: value as Definition }
  } catch (failure) {
    return { error: `Not valid JSON: ${errorMessage(failure)}` }
  }
}

function ReportView({ report }: { report: SchemaValidationReport }) {
  const compatibility = COMPATIBILITY[report.compatibility]
  return (
    <div className={`notice ${report.valid ? 'ok' : 'danger'}`} role="status">
      <p>
        <strong>{report.valid ? 'The definition is valid.' : 'The definition is not valid.'}</strong>{' '}
        {compatibility && <Badge tone={compatibility.tone}>{compatibility.label}</Badge>}
      </p>
      {report.errors.length > 0 && (
        <ul>
          {report.errors.map((item, index) => (
            <li key={index}>• {item}</li>
          ))}
        </ul>
      )}
      {report.migration_plan.length > 0 && (
        <>
          <p>What would need to happen first:</p>
          <ol>
            {report.migration_plan.map((step, index) => (
              <li key={index}>
                {index + 1}. {step}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}

/**
 * Apply a new definition in one go: check it, save it as a new version, and (for
 * an owner) publish it. A change that is not purely additive is never published
 * from here; it is reported so nothing existing is put at risk.
 */
function useApplyDefinition(onDone: () => void) {
  const { scope, canAdmin } = useProject()
  const invalidate = useInvalidateProject(scope)
  const [report, setReport] = useState<SchemaValidationReport | null>(null)
  const [savedDraft, setSavedDraft] = useState<number | null>(null)
  const apply = useMutation({
    mutationFn: async (definition: Definition) => {
      setSavedDraft(null)
      const checked = await api.validateSchema(scope, definition)
      setReport(checked)
      if (!checked.valid || checked.compatibility !== 'additive') return null
      const draft = await api.createSchemaDraft(scope, definition)
      if (!canAdmin) {
        setSavedDraft(draft.version.version)
        return null
      }
      return api.publishSchema(scope, draft.version.id)
    },
    onSuccess: async (published) => {
      await invalidate()
      if (published) onDone()
    },
  })
  const blocked = report !== null && (!report.valid || report.compatibility !== 'additive')
  return { apply, report: blocked ? report : null, savedDraft }
}

function ApplyOutcome({
  state,
}: {
  state: ReturnType<typeof useApplyDefinition>
}) {
  return (
    <>
      {state.report && <ReportView report={state.report} />}
      {state.savedDraft !== null && (
        <p className="notice" role="status">
          Saved as draft version {state.savedDraft}. A project owner needs to publish it before it
          takes effect.
        </p>
      )}
      <ErrorNotice error={state.apply.error} prefix="Not saved" />
    </>
  )
}

function TypeDialog({
  definition,
  existing,
  taken,
  onClose,
}: {
  definition: Definition
  /** The custom type being edited, if any. */
  existing?: Record<string, unknown>
  taken: string[]
  onClose: () => void
}) {
  const [name, setName] = useState(existing ? words(String(existing.entity_type)) : '')
  const [description, setDescription] = useState(String(existing?.description ?? ''))
  const [rows, setRows] = useState<FieldRow[]>(
    ((existing?.fields as Record<string, unknown>[] | undefined) ?? []).map((field) => ({
      label: words(String(field.name)),
      kind: String(field.field_type),
      values: ((field.values as string[] | undefined) ?? []).join(', '),
      required: Boolean(field.required),
    })),
  )
  const [problem, setProblem] = useState<string | null>(null)
  const state = useApplyDefinition(onClose)
  const identifier = existing ? String(existing.entity_type) : typeIdentifier(name)

  const setRow = (index: number, patch: Partial<FieldRow>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!identifier) return setProblem('Give the type a name, such as “Ship”.')
    if (!existing && taken.includes(identifier)) return setProblem(`You already have a type called ${identifier}.`)
    const fields: Record<string, unknown>[] = []
    const seen = new Set<string>()
    for (const row of rows) {
      if (!row.label.trim()) continue
      const fieldName = fieldIdentifier(row.label)
      if (!fieldName) return setProblem(`“${row.label}” needs at least one letter.`)
      if (seen.has(fieldName)) return setProblem(`“${row.label}” is listed twice.`)
      seen.add(fieldName)
      const values = row.values.split(',').map((value) => value.trim()).filter(Boolean)
      if (row.kind === 'enum' && values.length === 0) {
        return setProblem(`List the choices for “${row.label}”, separated by commas.`)
      }
      fields.push({
        name: fieldName,
        field_type: row.kind,
        required: row.required,
        ...(row.kind === 'enum' ? { values } : {}),
      })
    }
    setProblem(null)
    const extension = {
      ...(existing ?? {}),
      entity_type: identifier,
      description: description.trim() || null,
      fields,
    }
    const others = list(definition, 'entity_extensions').filter((item) => item.entity_type !== identifier)
    state.apply.mutate({ ...definition, entity_extensions: [...others, extension] })
  }

  return (
    <Dialog title={existing ? `Edit ${words(identifier)}` : 'Add a type of entry'} onClose={onClose} wide>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field
          label="What is it called?"
          hint={
            existing
              ? 'A type keeps the name it was created with.'
              : identifier && identifier !== name.trim()
                ? `Saved as “${identifier}”.`
                : 'One thing, in the singular: Ship, Spell, Bloodline.'
          }
        >
          {(props) => (
            <input
              {...props}
              data-autofocus
              value={name}
              disabled={Boolean(existing)}
              placeholder="Ship"
              onChange={(event) => setName(event.target.value)}
            />
          )}
        </Field>
        <Field label="Description (optional)">
          {(props) => (
            <input {...props} value={description} onChange={(event) => setDescription(event.target.value)} />
          )}
        </Field>

        <fieldset className="stack tight">
          <legend>Fields</legend>
          <p className="hint small muted">
            The details every entry of this type can have. You can add more later; removing one is
            harder once entries use it.
          </p>
          {rows.map((row, index) => (
            <div key={index} className="card quiet stack tight" style={{ padding: '0.8rem' }}>
              <div className="grid-2">
                <label className="mini">
                  <span>Name</span>
                  <input
                    aria-label={`Field ${index + 1} name`}
                    placeholder="Home port"
                    value={row.label}
                    onChange={(event) => setRow(index, { label: event.target.value })}
                  />
                </label>
                <label className="mini">
                  <span>Kind</span>
                  <select
                    aria-label={`Field ${index + 1} kind`}
                    value={row.kind}
                    onChange={(event) => setRow(index, { kind: event.target.value })}
                  >
                    {FIELD_KINDS.map((kind) => (
                      <option key={kind.value} value={kind.value}>
                        {kind.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {row.kind === 'enum' && (
                <label className="mini">
                  <span>Choices, separated by commas</span>
                  <input
                    aria-label={`Field ${index + 1} choices, separated by commas`}
                    placeholder="sloop, brig, galleon"
                    value={row.values}
                    onChange={(event) => setRow(index, { values: event.target.value })}
                  />
                </label>
              )}
              <div className="actions" style={{ justifyContent: 'space-between' }}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={row.required}
                    onChange={(event) => setRow(index, { required: event.target.checked })}
                  />
                  Must be filled in
                </label>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove field ${index + 1}`}
                  onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <div>
            <Button
              size="sm"
              icon="plus"
              onClick={() => setRows((current) => [...current, { label: '', kind: 'string', values: '', required: false }])}
            >
              Add a field
            </Button>
          </div>
        </fieldset>

        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <ApplyOutcome state={state} />
        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={state.apply.isPending}>
            {state.apply.isPending ? 'Saving…' : existing ? 'Save type' : 'Add type'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function FactKindDialog({
  definition,
  types,
  taken,
  onClose,
}: {
  definition: Definition
  types: string[]
  taken: string[]
  onClose: () => void
}) {
  const [phrase, setPhrase] = useState('')
  const [subjects, setSubjects] = useState<string[]>([])
  const [kind, setKind] = useState('entity_ref')
  const [objects, setObjects] = useState<string[]>([])
  const [single, setSingle] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const state = useApplyDefinition(onClose)
  const identifier = predicateIdentifier(phrase)

  const toggle = (current: string[], type: string) =>
    current.includes(type) ? current.filter((item) => item !== type) : [...current, type]

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!identifier) return setProblem('Write the phrase, such as “trained by”.')
    if (taken.includes(identifier)) return setProblem('You already have a kind of fact with that phrase.')
    if (subjects.length === 0) return setProblem('Choose at least one type it can be about.')
    if (kind === 'entity_ref' && objects.length === 0) {
      return setProblem('Choose at least one type it can point to.')
    }
    setProblem(null)
    const extension = {
      predicate: identifier,
      description: null,
      subject_types: subjects,
      object_kind: kind,
      object_types: kind === 'entity_ref' ? objects : [],
      cardinality: single ? 'one' : 'many',
      temporal_allowed: false,
    }
    state.apply.mutate({
      ...definition,
      predicate_extensions: [...list(definition, 'predicate_extensions'), extension],
    })
  }

  const example = `${subjects[0] ? typeLabel(subjects[0]) : 'Someone'} ${
    identifier ? predicateLabel(identifier) : '…'
  } ${kind === 'entity_ref' ? (objects[0] ? typeLabel(objects[0]).toLowerCase() : '…') : '…'}`

  return (
    <Dialog title="Add a kind of fact" onClose={onClose} wide>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field label="The phrase in the middle of the sentence" hint={`Reads as: ${example}`}>
          {(props) => (
            <input
              {...props}
              data-autofocus
              placeholder="was trained by"
              value={phrase}
              onChange={(event) => setPhrase(event.target.value)}
            />
          )}
        </Field>
        <fieldset>
          <legend>Who or what can it be about?</legend>
          <div className="chips">
            {types.map((type) => (
              <label key={type} className={`chip ${subjects.includes(type) ? 'on' : ''}`}>
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={subjects.includes(type)}
                  onChange={() => setSubjects(toggle(subjects, type))}
                />
                {typeLabel(type)}
              </label>
            ))}
          </div>
        </fieldset>
        <Field label="What does it point to?">
          {(props) => (
            <select {...props} value={kind} onChange={(event) => setKind(event.target.value)}>
              {OBJECT_KINDS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        {kind === 'entity_ref' && (
          <fieldset>
            <legend>Which types of entry?</legend>
            <div className="chips">
              {types.map((type) => (
                <label key={type} className={`chip ${objects.includes(type) ? 'on' : ''}`}>
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={objects.includes(type)}
                    onChange={() => setObjects(toggle(objects, type))}
                  />
                  {typeLabel(type)}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="check">
          <input type="checkbox" checked={single} onChange={(event) => setSingle(event.target.checked)} />
          Only one can be true at a time (like “is located at”)
        </label>

        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <ApplyOutcome state={state} />
        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={state.apply.isPending}>
            {state.apply.isPending ? 'Saving…' : 'Add kind of fact'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function PublishProblem({ error }: { error: unknown }) {
  if (!error) return null
  if (isApiError(error) && (error.status === 409 || error.status === 403)) {
    return (
      <p className="notice danger" role="alert">
        <span>
          <strong>
            {error.status === 403 ? 'You are not allowed to publish.' : 'This version cannot be published.'}
          </strong>{' '}
          {error.message} Your story was not changed.
        </span>
      </p>
    )
  }
  return <ErrorNotice error={error} prefix="Not published" />
}

/** The full definition as JSON, with versions: everything the friendly forms do, and more. */
function Advanced({ versions, published }: { versions: SchemaVersion[]; published?: SchemaVersion }) {
  const { scope, canWrite, canAdmin } = useProject()
  const invalidate = useInvalidateProject(scope)
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [report, setReport] = useState<SchemaValidationReport | null>(null)
  const draftText = text ?? JSON.stringify(published?.definition ?? EMPTY, null, 2)

  const validate = useMutation({
    mutationFn: (definition: Definition) => api.validateSchema(scope, definition),
    onSuccess: (result) => setReport(result),
    onError: (failure) => {
      // Older servers reject an ill-formed definition outright instead of reporting it.
      setReport({
        valid: false,
        content_hash: '',
        compatibility: 'invalid',
        errors: [errorMessage(failure)],
        migration_plan: [],
        entity_types: [],
        predicates: [],
      })
    },
  })
  const createDraft = useMutation({
    mutationFn: (definition: Definition) => api.createSchemaDraft(scope, definition),
    onSuccess: () => invalidate(),
  })
  const [publishing, setPublishing] = useState<SchemaVersion | null>(null)
  const publish = useMutation({
    mutationFn: (version: SchemaVersion) => api.publishSchema(scope, version.id),
    onSuccess: () => invalidate(),
  })

  const withDefinition = (action: (definition: Definition) => void) => {
    const parsed = parseDefinition(draftText)
    setProblem(parsed.error ?? null)
    if (parsed.definition) action(parsed.definition)
  }

  return (
    <div className="stack">
      <p className="muted small">
        Types and kinds of fact are stored as one definition with numbered versions. A published
        version never changes, and each entry remembers the version it was saved under.
      </p>
      <ul className="rows">
        {versions.map((version) => (
          <li className="row" key={version.id}>
            <span className="grow">
              <strong>Version {version.version}</strong>{' '}
              <span className="muted">{String(version.definition?.label ?? '')}</span>
            </span>
            <Badge tone={version.status === 'published' ? 'ok' : 'neutral'}>
              {version.status === 'published' ? 'Published' : 'Draft'}
            </Badge>
            {version.status !== 'published' && (
              <Button size="sm" onClick={() => setText(JSON.stringify(version.definition ?? EMPTY, null, 2))}>
                Load into editor
              </Button>
            )}
            {version.status !== 'published' && canAdmin && (
              <Button size="sm" variant="primary" onClick={() => setPublishing(version)}>
                Publish version {version.version}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {!canAdmin && versions.some((version) => version.status !== 'published') && (
        <p className="muted small">Only a project owner can publish a draft.</p>
      )}
      {publishing ? (
        <ConfirmDialog
          title={`Publish version ${publishing.version}?`}
          confirmLabel={`Publish version ${publishing.version}`}
          keepLabel="Not yet"
          danger={false}
          busy={publish.isPending}
          onCancel={() => {
            publish.reset()
            setPublishing(null)
          }}
          onConfirm={() => publish.mutate(publishing, { onSuccess: () => setPublishing(null) })}
        >
          <p>
            New entries and facts will follow this version from now on. A published version can
            never be edited or unpublished; to change it later you publish another.
          </p>
          <PublishProblem error={publish.error} />
        </ConfirmDialog>
      ) : (
        <PublishProblem error={publish.error} />
      )}

      <Field
        label="Definition (JSON)"
        hint="Types with typed fields, and kinds of fact. It cannot contain code or patterns."
        error={problem}
      >
        {(props) => (
          <textarea
            {...props}
            className="code"
            rows={14}
            spellCheck={false}
            value={draftText}
            readOnly={!canWrite}
            onChange={(event) => {
              setText(event.target.value)
              setReport(null)
            }}
          />
        )}
      </Field>
      {canWrite && (
        <div className="actions">
          <Button disabled={validate.isPending} onClick={() => withDefinition((d) => validate.mutate(d))}>
            Check definition
          </Button>
          <Button
            disabled={createDraft.isPending || !report?.valid}
            onClick={() => withDefinition((d) => createDraft.mutate(d))}
          >
            Save as draft
          </Button>
          {!report?.valid && <span className="hint">Check the definition before saving it.</span>}
        </div>
      )}
      {report && <ReportView report={report} />}
      {createDraft.isSuccess && (
        <p className="notice" role="status">
          Saved as draft version {createDraft.data.version.version}. It changes nothing until it is
          published.
        </p>
      )}
      <ErrorNotice error={createDraft.error} prefix="Draft not saved" />
      {published && (
        <details className="more">
          <summary>Published definition (version {published.version})</summary>
          <Json value={published.definition} />
        </details>
      )}
    </div>
  )
}

/** Shape the story bible: which types of entry exist, their fields, and the kinds of fact. */
export function CustomizePage() {
  const { scope, canWrite } = useProject()
  const versions = useQuery({
    queryKey: scopedKey(scope, 'schemas'),
    queryFn: () => api.listSchemaVersions(scope),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const [typeDialog, setTypeDialog] = useState<{ existing?: Record<string, unknown> } | null>(null)
  const [factDialog, setFactDialog] = useState(false)

  const items = [...(versions.data ?? [])].sort((a, b) => b.version - a.version)
  const published = items.find((version) => version.status === 'published')
  const definition = (published?.definition ?? EMPTY) as Definition
  const types = vocabulary.data?.entity_types ?? []
  const predicates = vocabulary.data?.predicates ?? []
  const extensions = list(definition, 'entity_extensions')
  const ready = versions.isSuccess && Boolean(published)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Types and fields</h1>
          <p className="lede">
            Your story bible comes with the usual kinds of entry. Add your own — ships, spells,
            bloodlines — and the kinds of fact that connect them.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        <ErrorNotice error={versions.error ?? vocabulary.error} prefix="Could not load the setup" />
        {(versions.isPending || vocabulary.isPending) && <Loading>Loading…</Loading>}
        {versions.isSuccess && !published && (
          <p className="notice warn" role="note">
            <span>
              <strong>No schema version is published yet.</strong> Nothing can be added to this
              project until one is. Open “Advanced” below, check the definition, save it as a draft
              and publish it.
            </span>
          </p>
        )}

        <section aria-labelledby="types-heading" className="stack">
          <div className="actions" style={{ justifyContent: 'space-between' }}>
            <h2 id="types-heading">Types of entry</h2>
            {canWrite && ready && (
              <Button icon="plus" onClick={() => setTypeDialog({})}>
                Add a type
              </Button>
            )}
          </div>
          <ul className="entry-grid">
            {types.map((type) => {
              const fields = schemaFields(type.attributes_schema)
              const extension = extensions.find((item) => item.entity_type === type.name)
              return (
                <li key={type.name} className="card stack tight">
                  <div className="actions">
                    <TypeAvatar type={type.name} />
                    <strong style={{ flex: 1 }}>{typeLabel(type.name)}</strong>
                    <Badge tone={type.core ? 'neutral' : 'accent'}>{type.core ? 'Built in' : 'Yours'}</Badge>
                  </div>
                  <p className="muted small">
                    {fields.length === 0
                      ? type.core
                        ? `${typeLook(type.name).blurb}. Any details you like.`
                        : 'Any details you like.'
                      : fields
                          .map((field) => `${words(field.name)} (${KIND_WORDS[field.kind] ?? field.kind})`)
                          .join(' · ')}
                  </p>
                  {canWrite && extension && (
                    <div>
                      <Button
                        size="sm"
                        icon="edit"
                        aria-label={`Edit ${typeLabel(type.name)}`}
                        onClick={() => setTypeDialog({ existing: extension })}
                      >
                        Edit
                      </Button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>

        <section aria-labelledby="facts-heading" className="stack">
          <div className="actions" style={{ justifyContent: 'space-between' }}>
            <h2 id="facts-heading">Kinds of fact</h2>
            {canWrite && ready && (
              <Button icon="plus" onClick={() => setFactDialog(true)}>
                Add a kind of fact
              </Button>
            )}
          </div>
          <ul className="card flush">
            {predicates.map((predicate) => (
              <li className="row" key={predicate.predicate} style={{ padding: '0.7rem 1.25rem' }}>
                <span className="grow sentence">
                  {predicate.subject_types.map((type) => typeLabel(type)).join(' / ')}{' '}
                  <span className="verb">{predicateLabel(predicate.predicate)}</span>{' '}
                  {predicate.object_shape === 'entity_ref'
                    ? predicate.object_types.map((type) => typeLabel(type).toLowerCase()).join(' / ') ||
                      'any entry'
                    : (KIND_WORDS[predicate.object_shape] ?? words(predicate.object_shape).toLowerCase())}
                </span>
                {predicate.cardinality === 'one' && <span className="muted small">one at a time</span>}
                <Badge tone={predicate.core ? 'neutral' : 'accent'}>{predicate.core ? 'Built in' : 'Yours'}</Badge>
              </li>
            ))}
          </ul>
        </section>

        {versions.isSuccess && (
          <details className="more">
            <summary>
              <Icon name="sliders" size={15} /> Advanced: versions and the raw definition
            </summary>
            <Advanced versions={items} published={published} />
          </details>
        )}
      </div>

      {typeDialog && (
        <TypeDialog
          definition={definition}
          existing={typeDialog.existing}
          taken={types.map((type) => type.name)}
          onClose={() => setTypeDialog(null)}
        />
      )}
      {factDialog && (
        <FactKindDialog
          definition={definition}
          types={types.map((type) => type.name)}
          taken={predicates.map((predicate) => predicate.predicate)}
          onClose={() => setFactDialog(false)}
        />
      )}
    </>
  )
}

