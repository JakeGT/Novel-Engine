import { useQueries, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type Belief, type CanonEntity } from '../api/client'
import { JSON_HINTS, buildObject, type Entered } from '../lib/facts'
import { objectText, predicateLabel, typeLabel, type Tone } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { ObjectControl } from './ObjectControl'
import { Badge, Button, ErrorNotice, UndoNotice, WriteProblem } from './ui'

const AGREEMENT: Record<string, { label: string; tone: Tone }> = {
  agrees: { label: 'True in your story', tone: 'ok' },
  disagrees: { label: 'Not so in your story', tone: 'danger' },
  unknown: { label: 'Your story doesn’t say', tone: 'neutral' },
}

// How sure a character is, in words. The number is what is stored.
const SURE: { value: string; label: string }[] = [
  { value: '1', label: 'is certain' },
  { value: '0.75', label: 'is fairly sure' },
  { value: '0.5', label: 'suspects' },
]

function sentence(belief: Belief, names: Map<string, string>): string {
  const subject = names.get(belief.subject_id) ?? 'an entry'
  const object = objectText(belief.object as Record<string, unknown>, names)
  return `${subject} ${predicateLabel(belief.predicate)} ${object}`
}

/**
 * What a character thinks is true, which may not be. Each belief is compared with
 * the story's facts when shown; nothing here records whether it is right.
 */
export function BeliefsPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const active = entity.lifecycle === 'active'
  const entries = useEntries(scope)
  const beliefs = useQuery({
    queryKey: scopedKey(scope, 'beliefs', entity.id),
    queryFn: () => api.listBeliefs(scope, { holder_entity_id: entity.id, limit: 200 }),
  })
  const items = beliefs.data?.items ?? []
  const agreements = useQueries({
    queries: items.map((belief) => ({
      queryKey: scopedKey(scope, 'truth-agreement', belief.id),
      queryFn: () => api.truthAgreement(scope, belief.id),
    })),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
    enabled: canWrite && active,
  })

  const [aboutId, setAboutId] = useState(entity.id)
  const [predicateName, setPredicateName] = useState('')
  const [entered, setEntered] = useState<Entered>({ value: '', unit: '' })
  const [sure, setSure] = useState('1')
  const [problem, setProblem] = useState<string | null>(null)
  const everyone = entries.items.filter((item) => item.lifecycle === 'active')
  const about = everyone.find((item) => item.id === aboutId) ?? entity
  const predicates = (vocabulary.data?.predicates ?? []).filter((item) =>
    item.subject_types.includes(about.type),
  )
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = everyone.filter(
    (item) =>
      item.id !== about.id &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )

  const add = useCanonWrite<Belief>(
    (expectedRevision) =>
      api.assertBelief(scope, {
        holder_entity_id: entity.id,
        proposition: {
          subject_id: about.id,
          predicate: predicateName,
          object: predicate ? (buildObject(predicate, entered, scope.projectId).object ?? {}) : {},
        },
        // Where the belief came from: entered by an author, not drafted or read from prose.
        source_ref: 'by-hand',
        confidence: Number(sure),
        expected_revision: expectedRevision,
      }),
    () => setEntered({ value: '', unit: '' }),
  )
  const [ended, setEnded] = useState<{ belief: Belief; text: string } | null>(null)
  const end = useCanonWrite<Belief, { belief: Belief; text: string }>(
    (expectedRevision, target) => api.closeBelief(scope, target.belief.id, expectedRevision),
    (_result, target) => setEnded(target),
  )
  // Ending a belief is put right by recording it again, so it is offered as Undo.
  const restore = useCanonWrite<Belief, Belief>(
    (expectedRevision, belief) =>
      api.assertBelief(scope, {
        holder_entity_id: belief.holder_entity_id,
        proposition: { subject_id: belief.subject_id, predicate: belief.predicate, object: belief.object },
        source_ref: belief.source_ref ?? 'by-hand',
        confidence: belief.confidence,
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

  const canAdd = canWrite && active
  if (items.length === 0 && !beliefs.isError && !canAdd) return null

  return (
    <section className="card" aria-labelledby="beliefs-heading">
      <div className="card-head">
        <div>
          <h2 id="beliefs-heading">What {entity.name} believes</h2>
          <p className="muted small">
            What {entity.name} thinks is true, which may not be. Each belief is compared with your
            facts every time you look.
          </p>
        </div>
      </div>
      <ErrorNotice error={beliefs.error} prefix="Could not load beliefs" />
      {beliefs.isSuccess && items.length === 0 && (
        <p className="muted">Nothing recorded about what {entity.name} believes yet.</p>
      )}
      <ul className="rows">
        {items.map((belief, index) => {
          const agreement = agreements[index]?.data?.truth_agreement
          const view = agreement ? AGREEMENT[agreement] : undefined
          const text = sentence(belief, entries.names)
          return (
            <li className="row" key={belief.id}>
              <span className="grow sentence">{text}</span>
              {belief.confidence < 1 && (
                <span className="muted small">{Math.round(belief.confidence * 100)}% sure</span>
              )}
              {view ? <Badge tone={view.tone}>{view.label}</Badge> : <span className="muted small">checking…</span>}
              {canAdd && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={end.pending}
                  aria-label={`No longer believes: ${text}`}
                  onClick={() => void end.submit({ belief, text })}
                >
                  No longer believes this
                </Button>
              )}
            </li>
          )
        })}
      </ul>
      {ended && (
        <UndoNotice busy={restore.pending} onUndo={() => void restore.submit(ended.belief)}>
          {entity.name} no longer believes “{ended.text}”. It stays in the history.
        </UndoNotice>
      )}
      <WriteProblem write={end} scope={scope} />
      <WriteProblem write={restore} scope={scope} />

      {canAdd && (
        <form className="stack tight" onSubmit={onSubmit} noValidate aria-label="Add a belief" style={{ marginTop: '1.1rem' }}>
          <hr className="divider" />
          <span className="label">Add a belief</span>
          <div className="actions">
            <strong>{entity.name}</strong>
            <span style={{ flex: '0 1 9rem' }}>
              <select aria-label="How sure" value={sure} disabled={add.pending} onChange={(event) => setSure(event.target.value)}>
                {SURE.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </span>
            <span>that</span>
            <span style={{ flex: '1 1 10rem' }}>
              <select
                aria-label="Who or what the belief is about"
                value={about.id}
                disabled={add.pending}
                onChange={(event) => {
                  setAboutId(event.target.value)
                  setPredicateName('')
                  setEntered({ value: '', unit: '' })
                }}
              >
                {everyone.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id === entity.id ? `${item.name} (themself)` : `${item.name} (${typeLabel(item.type)})`}
                  </option>
                ))}
              </select>
            </span>
            <span style={{ flex: '1 1 10rem' }}>
              <select
                aria-label="How the sentence continues"
                value={predicate ? predicateName : ''}
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
                  </option>
                ))}
              </select>
            </span>
            {predicate && (
              <span style={{ flex: '1 1 10rem', display: 'flex', gap: '0.5rem' }}>
                <ObjectControl
                  predicate={predicate}
                  entered={entered}
                  onChange={setEntered}
                  targets={targets}
                  disabled={add.pending}
                  label={`What ${about.name} ${predicateLabel(predicate.predicate)}`}
                />
              </span>
            )}
            <Button variant="primary" type="submit" disabled={add.pending}>
              {add.pending ? 'Adding…' : 'Add belief'}
            </Button>
          </div>
          {predicate && JSON_HINTS[predicate.object_shape] && (
            <span className="hint small muted">{JSON_HINTS[predicate.object_shape]}</span>
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
