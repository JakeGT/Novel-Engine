import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type CanonEntity, type Fact, type VocabularyPredicate } from '../api/client'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { ErrorNotice, Field, WriteProblem } from './ui'

function objectText(object: Record<string, unknown>, names: Map<string, string>): string {
  if (object.kind === 'entity_ref') {
    const target = String(object.entity_id)
    return names.get(target) ?? target
  }
  if (object.kind === 'quantity') return `${String(object.value)} ${String(object.unit)}`
  if (object.kind === 'structured') return JSON.stringify(object.value)
  return String(object.value)
}

function buildObject(
  predicate: VocabularyPredicate,
  value: string,
  projectId: string,
): { object?: Record<string, unknown>; error?: string } {
  const text = value.trim()
  if (!text) return { error: 'A value is required.' }
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
    default:
      // Enum, quantity, and structured values carry extra fields; enter the object as JSON.
      try {
        return { object: { kind: predicate.object_shape, ...(JSON.parse(text) as object) } }
      } catch {
        return { error: 'Enter the value as a JSON object.' }
      }
  }
}

const JSON_HINTS: Record<string, string> = {
  enum: 'JSON, for example {"value": "wary", "allowed": ["wary", "open"]}',
  quantity: 'JSON, for example {"value": "12.5", "unit": "km"}',
  structured: 'JSON, for example {"value": {"note": "…"}}',
}

/** Objective facts where this entity is the subject or the object, with assert and close. */
export function RelationshipsPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const active = entity.lifecycle === 'active'

  // The server filters by the entity on each side, so a large project is complete.
  const outgoingFacts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'subject', entity.id),
    queryFn: () => api.listFacts(scope, { subject_id: entity.id, limit: 200 }),
  })
  const incomingFacts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'object', entity.id),
    queryFn: () => api.listFacts(scope, { object_entity_id: entity.id, limit: 200 }),
  })
  const entities = useQuery({
    queryKey: scopedKey(scope, 'entities', '', '', true),
    queryFn: () => api.listEntities(scope, { include_retired: true, limit: 200 }),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })

  const names = new Map((entities.data?.items ?? []).map((item) => [item.id, item.name]))
  const outgoing = outgoingFacts.data?.items ?? []
  const incoming = incomingFacts.data?.items ?? []

  const predicates = (vocabulary.data?.predicates ?? []).filter((predicate) =>
    predicate.subject_types.includes(entity.type),
  )
  const [predicateName, setPredicateName] = useState('')
  const [value, setValue] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = (entities.data?.items ?? []).filter(
    (item) =>
      item.lifecycle === 'active' &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )

  const assert = useCanonWrite<Fact>(
    (expectedRevision) => {
      const built = buildObject(predicate as VocabularyPredicate, value, scope.projectId)
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
    () => setValue(''),
  )

  const close = useCanonWrite<Fact, string>((expectedRevision, factId) =>
    api.closeFact(scope, factId, expectedRevision),
  )

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!predicate) return setProblem('Choose a predicate.')
    const built = buildObject(predicate, value, scope.projectId)
    setProblem(built.error ?? null)
    if (!built.error) void assert.submit()
  }

  const row = (fact: Fact, direction: 'out' | 'in') => (
    <li key={fact.id}>
      {direction === 'out' ? (
        <>
          <strong>{fact.predicate}</strong> → {objectText(fact.object, names)}
        </>
      ) : (
        <>
          {names.get(fact.subject_id) ?? fact.subject_id} <strong>{fact.predicate}</strong> → this
        </>
      )}{' '}
      <span className="muted">since r{fact.created_revision}</span>{' '}
      {canWrite && fact.status === 'asserted' && (
        <button
          type="button"
          className="button secondary"
          disabled={close.pending}
          aria-label={`Close fact ${fact.predicate} ${objectText(fact.object, names)}`}
          onClick={() => void close.submit(fact.id)}
        >
          Close
        </button>
      )}
    </li>
  )

  return (
    <section className="panel" aria-labelledby="relationships-heading">
      <h3 id="relationships-heading">Facts and relationships</h3>
      <p className="muted">Objective canon. What a character believes is listed separately.</p>
      <ErrorNotice
        error={outgoingFacts.error ?? incomingFacts.error}
        prefix="Could not load facts"
      />
      {outgoing.length + incoming.length === 0 &&
        !outgoingFacts.isLoading &&
        !incomingFacts.isLoading && (
        <p className="muted">No open facts mention this entity.</p>
      )}
      <ul className="list">
        {outgoing.map((fact) => row(fact, 'out'))}
        {incoming.map((fact) => row(fact, 'in'))}
      </ul>
      <WriteProblem write={close} scope={scope} />

      {canWrite && active && (
        <form className="stack" onSubmit={onSubmit} noValidate aria-label="Assert fact">
          <h4>Assert a fact</h4>
          <Field label="Predicate">
            {(props) => (
              <select
                {...props}
                value={predicateName}
                onChange={(event) => {
                  setPredicateName(event.target.value)
                  setValue('')
                }}
              >
                <option value="">Choose…</option>
                {predicates.map((item) => (
                  <option key={item.predicate} value={item.predicate}>
                    {item.predicate}
                    {item.cardinality === 'one' ? ' (single value)' : ''}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {predicate && (
            <Field label="Value" error={problem} hint={JSON_HINTS[predicate.object_shape]}>
              {(props) =>
                predicate.object_shape === 'entity_ref' ? (
                  <select {...props} value={value} onChange={(event) => setValue(event.target.value)}>
                    <option value="">Choose an entity…</option>
                    {targets.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} ({item.type})
                      </option>
                    ))}
                  </select>
                ) : predicate.object_shape === 'boolean' ? (
                  <select {...props} value={value} onChange={(event) => setValue(event.target.value)}>
                    <option value="">Choose…</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <input {...props} value={value} onChange={(event) => setValue(event.target.value)} />
                )
              }
            </Field>
          )}
          {!predicate && problem && (
            <p className="error" role="alert">
              {problem}
            </p>
          )}
          <button className="button" type="submit" disabled={assert.pending}>
            Assert fact
          </button>
          <WriteProblem write={assert} scope={scope} />
        </form>
      )}
    </section>
  )
}
