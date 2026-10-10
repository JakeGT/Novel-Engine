import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, type CanonEntity, type Fact, type VocabularyPredicate } from '../api/client'
import { objectText, predicateLabel, typeLabel, typePlural } from '../lib/words'
import { entryPath, scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Button, ErrorNotice, Loading, UndoNotice, WriteProblem } from './ui'

interface Entered {
  value: string
  unit: string
}

function buildObject(
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

const JSON_HINTS: Record<string, string> = {
  enum: 'Enter as JSON, for example {"value": "wary", "allowed": ["wary", "open"]}',
  structured: 'Enter as JSON, for example {"value": {"note": "…"}}',
}

/** What is true about this entry, read as sentences, with a sentence builder to add more. */
export function FactsPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const active = entity.lifecycle === 'active'
  const entries = useEntries(scope)

  // The server filters by the entry on each side, so a large project is complete.
  const outgoingFacts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'subject', entity.id),
    queryFn: () => api.listFacts(scope, { subject_id: entity.id, limit: 200 }),
  })
  const incomingFacts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'object', entity.id),
    queryFn: () => api.listFacts(scope, { object_entity_id: entity.id, limit: 200 }),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })

  const outgoing = outgoingFacts.data?.items ?? []
  const incoming = incomingFacts.data?.items ?? []
  const loading = outgoingFacts.isPending || incomingFacts.isPending

  const predicates = (vocabulary.data?.predicates ?? []).filter((predicate) =>
    predicate.subject_types.includes(entity.type),
  )
  const [predicateName, setPredicateName] = useState('')
  const [entered, setEntered] = useState<Entered>({ value: '', unit: '' })
  const [problem, setProblem] = useState<string | null>(null)
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = entries.items.filter(
    (item) =>
      item.lifecycle === 'active' &&
      item.id !== entity.id &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )

  const add = useCanonWrite<Fact>(
    (expectedRevision) => {
      const built = buildObject(predicate as VocabularyPredicate, entered, scope.projectId)
      return api.assertFact(scope, {
        proposition: {
          subject_id: entity.id,
          predicate: predicateName,
          object: built.object ?? {},
        },
        confidence: 1,
        expected_revision: expectedRevision,
      })
    },
    () => setEntered({ value: '', unit: '' }),
  )
  const [ended, setEnded] = useState<{ fact: Fact; sentence: string } | null>(null)
  const end = useCanonWrite<Fact, { fact: Fact; sentence: string }>(
    (expectedRevision, target) => api.closeFact(scope, target.fact.id, expectedRevision),
    (_result, target) => setEnded(target),
  )
  // Ending a fact is put right by stating it again, so it is offered as Undo rather than confirmed.
  const restore = useCanonWrite<Fact, Fact>(
    (expectedRevision, fact) =>
      api.assertFact(scope, {
        proposition: { subject_id: fact.subject_id, predicate: fact.predicate, object: fact.object },
        confidence: fact.confidence,
        expected_revision: expectedRevision,
      }),
    () => setEnded(null),
  )

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!predicate) return setProblem('Choose how the sentence continues.')
    const built = buildObject(predicate, entered, scope.projectId)
    setProblem(built.error ?? null)
    if (!built.error) void add.submit()
  }

  const linked = (id: string) => (
    <Link to={entryPath(scope, id)}>{entries.names.get(id) ?? 'an entry'}</Link>
  )

  const row = (fact: Fact, direction: 'out' | 'in') => {
    const object = fact.object as Record<string, unknown>
    const text = objectText(object, entries.names)
    return (
      <li className="row" key={fact.id}>
        <span className="grow sentence">
          {direction === 'out' ? (
            <>
              {entity.name} <span className="verb">{predicateLabel(fact.predicate)}</span>{' '}
              {object.kind === 'entity_ref' ? linked(String(object.entity_id)) : <strong>{text}</strong>}
            </>
          ) : (
            <>
              {linked(fact.subject_id)} <span className="verb">{predicateLabel(fact.predicate)}</span>{' '}
              <strong>{entity.name}</strong>
            </>
          )}
        </span>
        <span className="muted small">since version {fact.created_revision}</span>
        {canWrite && fact.status === 'asserted' && (
          <Button
            size="sm"
            variant="ghost"
            disabled={end.pending}
            aria-label={`End fact: ${predicateLabel(fact.predicate)} ${text}`}
            onClick={() =>
              void end.submit({
                fact,
                sentence:
                  direction === 'out'
                    ? `${entity.name} ${predicateLabel(fact.predicate)} ${text}`
                    : `${entries.names.get(fact.subject_id) ?? 'An entry'} ${predicateLabel(fact.predicate)} ${entity.name}`,
              })
            }
          >
            No longer true
          </Button>
        )}
      </li>
    )
  }

  const control = () => {
    if (!predicate) return null
    const change = (value: string) => setEntered({ ...entered, value })
    // Locked while a fact is being saved, so the reset afterwards cannot wipe new input.
    const disabled = add.pending
    const label = `What ${entity.name} ${predicateLabel(predicate.predicate)}`
    if (predicate.object_shape === 'entity_ref') {
      return (
        <select aria-label={label} value={entered.value} disabled={disabled} onChange={(event) => change(event.target.value)}>
          <option value="">Choose…</option>
          {targets.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} ({typeLabel(item.type)})
            </option>
          ))}
        </select>
      )
    }
    if (predicate.object_shape === 'boolean') {
      return (
        <select aria-label={label} value={entered.value} disabled={disabled} onChange={(event) => change(event.target.value)}>
          <option value="">Choose…</option>
          <option value="true">yes</option>
          <option value="false">no</option>
        </select>
      )
    }
    if (predicate.object_shape === 'quantity') {
      return (
        <>
          <label className="mini">
            <span>Amount</span>
            <input
              aria-label="Amount"
              disabled={disabled}
              inputMode="decimal"
              placeholder="12.5"
              value={entered.value}
              onChange={(event) => change(event.target.value)}
            />
          </label>
          <label className="mini">
            <span>Unit</span>
            <input
              aria-label="Unit"
              disabled={disabled}
              placeholder="km"
              value={entered.unit}
              onChange={(event) => setEntered({ ...entered, unit: event.target.value })}
            />
          </label>
        </>
      )
    }
    return (
      <input
        aria-label={label}
        disabled={disabled}
        inputMode={['integer', 'decimal'].includes(predicate.object_shape) ? 'decimal' : undefined}
        value={entered.value}
        onChange={(event) => change(event.target.value)}
      />
    )
  }

  const noTargets = predicate?.object_shape === 'entity_ref' && targets.length === 0

  return (
    <section className="card" aria-labelledby="facts-heading">
      <div className="card-head">
        <div>
          <h2 id="facts-heading">Facts</h2>
          <p className="muted small">What is true in your story. What a character merely believes is listed separately.</p>
        </div>
      </div>
      <ErrorNotice error={outgoingFacts.error ?? incomingFacts.error} prefix="Could not load facts" />
      {loading && <Loading>Loading facts…</Loading>}
      {!loading && outgoing.length + incoming.length === 0 && (
        <p className="muted">Nothing recorded about {entity.name} yet.</p>
      )}
      <ul className="rows">
        {outgoing.map((fact) => row(fact, 'out'))}
        {incoming.map((fact) => row(fact, 'in'))}
      </ul>
      {ended && (
        <UndoNotice busy={restore.pending} onUndo={() => void restore.submit(ended.fact)}>
          No longer true: “{ended.sentence}”. It stays in the history.
        </UndoNotice>
      )}
      <WriteProblem write={end} scope={scope} />
      <WriteProblem write={restore} scope={scope} />

      {canWrite && active && predicates.length > 0 && (
        <form className="stack tight" onSubmit={onSubmit} noValidate aria-label="Add a fact" style={{ marginTop: '1.1rem' }}>
          <hr className="divider" />
          <span className="label">Add a fact</span>
          <div className="actions">
            <strong>{entity.name}</strong>
            <span style={{ flex: '1 1 11rem' }}>
              <select
                aria-label="How the sentence continues"
                value={predicateName}
                disabled={add.pending}
                onChange={(event) => {
                  setPredicateName(event.target.value)
                  setEntered({ value: '', unit: '' })
                  setProblem(null)
                }}
              >
                <option value="">Choose…</option>
                {predicates.map((item) => (
                  <option key={item.predicate} value={item.predicate}>
                    {predicateLabel(item.predicate)}
                    {item.cardinality === 'one' ? ' (only one at a time)' : ''}
                  </option>
                ))}
              </select>
            </span>
            {predicate && <span style={{ flex: '1 1 11rem', display: 'flex', gap: '0.5rem' }}>{control()}</span>}
            <Button variant="primary" type="submit" disabled={add.pending || noTargets}>
              {add.pending ? 'Adding…' : 'Add fact'}
            </Button>
          </div>
          {predicate && JSON_HINTS[predicate.object_shape] && (
            <span className="hint small muted">{JSON_HINTS[predicate.object_shape]}</span>
          )}
          {noTargets && predicate && (
            <p className="hint small muted">
              You have nothing this can point to yet. Add{' '}
              {predicate.object_types.length > 0
                ? predicate.object_types.map((type) => typePlural(type).toLowerCase()).join(' or ')
                : 'another entry'}{' '}
              first.
            </p>
          )}
          {problem && (
            <p className="error-text" role="alert">
              {problem}
            </p>
          )}
          <WriteProblem write={add} scope={scope} />
        </form>
      )}
    </section>
  )
}
