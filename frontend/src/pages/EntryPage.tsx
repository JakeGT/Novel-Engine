import { useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  isApiError,
  type Belief,
  type CanonEntity,
  type CharacterQuestionSheet,
} from '../api/client'
import { ActivityPanel } from '../components/ActivityPanel'
import { ArchiveEntry } from '../components/ArchiveEntry'
import { EntityForm } from '../components/EntityForm'
import { FactsPanel } from '../components/FactsPanel'
import { Icon } from '../components/icons'
import { MediaLinksPanel } from '../components/MediaLinksPanel'
import {
  Badge,
  Button,
  Dialog,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
  TypeAvatar,
} from '../components/ui'
import { objectText, predicateLabel, typeLabel, typePlural, valueText, words, type Tone } from '../lib/words'
import { scopedKey, useEntries, useInvalidateProject, useProject } from '../project'

const AGREEMENT: Record<string, { label: string; tone: Tone }> = {
  agrees: { label: 'True in canon', tone: 'ok' },
  disagrees: { label: 'Contradicts canon', tone: 'danger' },
  unknown: { label: 'Canon doesn’t say', tone: 'neutral' },
}

function Details({ entity, onEdit }: { entity: CanonEntity; onEdit?: () => void }) {
  const attributes = Object.entries(entity.attributes ?? {})
  return (
    <section className="card" aria-labelledby="details-heading">
      <div className="card-head">
        <h2 id="details-heading">Details</h2>
        {onEdit && (
          <Button size="sm" icon="edit" onClick={onEdit}>
            Edit
          </Button>
        )}
      </div>
      {attributes.length === 0 ? (
        <p className="muted">
          No details yet.{onEdit ? ' Use Edit to add lasting traits like a role, an age or a look.' : ''}
        </p>
      ) : (
        <dl className="props">
          {attributes.map(([key, value]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt>{words(key)}</dt>
              <dd>{valueText(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <details className="more" style={{ marginTop: '1rem' }}>
        <summary>Technical details</summary>
        <dl className="props small">
          <dt>Entry ID</dt>
          <dd>
            <code>{entity.id}</code>
          </dd>
          <dt>Short name</dt>
          <dd>{entity.slug ?? '—'}</dd>
          <dt>Added in</dt>
          <dd>Version {entity.created_revision}</dd>
          {entity.retired_revision != null && (
            <>
              <dt>Archived in</dt>
              <dd>Version {entity.retired_revision}</dd>
            </>
          )}
        </dl>
      </details>
    </section>
  )
}

function beliefSentence(belief: Belief, names: Map<string, string>): string {
  const subject = names.get(belief.subject_id) ?? 'an entry'
  const object = objectText(belief.object as Record<string, unknown>, names)
  return `${subject} ${predicateLabel(belief.predicate)} ${object}`
}

function Beliefs({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
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
  if (items.length === 0 && !beliefs.isError) return null
  return (
    <section className="card" aria-labelledby="beliefs-heading">
      <div className="card-head">
        <div>
          <h2 id="beliefs-heading">What {entity.name} believes</h2>
          <p className="muted small">
            Each belief is compared with your current facts every time you look, so this is always
            up to date.
          </p>
        </div>
      </div>
      <ErrorNotice error={beliefs.error} prefix="Could not load beliefs" />
      <ul className="rows">
        {items.map((belief, index) => {
          const agreement = agreements[index]?.data?.truth_agreement
          const view = agreement ? AGREEMENT[agreement] : undefined
          return (
            <li className="row" key={belief.id}>
              <span className="grow sentence">{beliefSentence(belief, entries.names)}</span>
              {belief.confidence < 1 && (
                <span className="muted small">{Math.round(belief.confidence * 100)}% sure</span>
              )}
              {view ? <Badge tone={view.tone}>{view.label}</Badge> : <span className="muted small">checking…</span>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Events({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const events = useQuery({
    queryKey: scopedKey(scope, 'events', 'entity', entity.id),
    queryFn: () => api.listEvents(scope, { entity_id: entity.id, limit: 200 }),
  })
  const taking = (events.data?.items ?? []).filter((event) =>
    event.participants.some((participant) => participant.entity_id === entity.id),
  )
  if (taking.length === 0) return null
  return (
    <section className="card" aria-labelledby="events-heading">
      <div className="card-head">
        <h2 id="events-heading">Events {entity.name} took part in</h2>
      </div>
      <ul className="rows">
        {taking.map((event) => (
          <li className="row" key={event.id}>
            <span className="grow">
              <strong>{event.name}</strong>{' '}
              <span className="muted">
                as{' '}
                {event.participants
                  .filter((participant) => participant.entity_id === entity.id)
                  .map((participant) => participant.role)
                  .join(', ')}
              </span>
            </span>
            <Badge>{words(event.kind)}</Badge>
            <span className="muted small">{event.world_position ?? 'date unknown'}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ProviderProblem({ error }: { error: unknown }) {
  const { scope, access } = useProject()
  if (!error) return null
  if (isApiError(error, 'capability_unavailable') || isApiError(error, 'provider_failure')) {
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>The interview needs an AI provider, and none is connected right now.</strong>{' '}
          Nothing was changed. You can still add details and facts by hand.
        </p>
        {access?.hosted && (
          <Link className="btn" to={`/projects/${scope.projectId}/assistant`}>
            Connect an AI key
          </Link>
        )}
      </div>
    )
  }
  return <ErrorNotice error={error} />
}

function Interview({ entity }: { entity: CanonEntity }) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const [sheet, setSheet] = useState<CharacterQuestionSheet | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})

  const questions = useMutation({
    mutationFn: () => api.characterQuestions(scope, entity.id),
    onSuccess: (data) => setSheet(data),
  })
  const proposal = useMutation({
    mutationFn: async () => {
      const answered = (sheet?.questions ?? [])
        .filter((question) => (answers[question.id] ?? '').trim())
        .map((question) => ({
          question_id: question.id,
          question: question.question,
          answer: answers[question.id].trim(),
        }))
      // A suggestion is not a canon write: aim it at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.characterProposal(scope, entity.id, { answers: answered, expected_revision: head })
    },
    onSuccess: async (result) => {
      await invalidate()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: { fromInterview: true, discarded: result.discarded },
      })
    },
  })
  const anyAnswer = Object.values(answers).some((answer) => answer.trim())

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    proposal.mutate()
  }

  return (
    <section className="card" aria-labelledby="interview-heading">
      <div className="card-head">
        <div>
          <h2 id="interview-heading">Interview {entity.name}</h2>
          <p className="muted small">
            The AI assistant asks a few questions and turns your answers into suggested changes.
            Nothing is added to your story until you review them.
          </p>
        </div>
        <span className="avatar sm tint-violet" aria-hidden="true">
          <Icon name="chat" size={15} />
        </span>
      </div>
      {!sheet && (
        <Button icon="spark" onClick={() => questions.mutate()} disabled={questions.isPending}>
          {questions.isPending ? 'Thinking of questions…' : 'Start the interview'}
        </Button>
      )}
      <div style={{ marginTop: questions.error ? '0.9rem' : 0 }}>
        <ProviderProblem error={questions.error} />
      </div>
      {sheet && (
        <form className="stack" onSubmit={onSubmit} aria-label="Interview answers">
          {sheet.questions.map((question) => (
            <div className="field" key={question.id}>
              <label htmlFor={`q-${question.id}`}>{question.question}</label>
              <textarea
                id={`q-${question.id}`}
                rows={2}
                value={answers[question.id] ?? ''}
                onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })}
              />
            </div>
          ))}
          <p className="hint small muted">Answer as many or as few as you like.</p>
          <div className="actions">
            <Button variant="primary" type="submit" disabled={!anyAnswer || proposal.isPending}>
              {proposal.isPending ? 'Writing suggestions…' : 'Turn answers into suggestions'}
            </Button>
          </div>
          <ProviderProblem error={proposal.error} />
        </form>
      )}
    </section>
  )
}

/** One entry of the story bible: what it is, what is true about it, and how it got that way. */
export function EntryPage() {
  const { scope, canWrite } = useProject()
  const { entityId = '' } = useParams()
  const [editing, setEditing] = useState(false)

  const query = useQuery({
    queryKey: scopedKey(scope, 'entity', entityId),
    queryFn: () => api.getEntity(scope, entityId),
  })
  const entity = query.data
  const active = entity?.lifecycle === 'active'
  const editable = canWrite && Boolean(active)

  return (
    <>
      <Link
        className="crumb"
        to={`/projects/${scope.projectId}/bible${entity ? `?type=${encodeURIComponent(entity.type)}` : ''}`}
      >
        <Icon name="arrowLeft" size={16} /> {entity ? typePlural(entity.type) : 'Story bible'}
      </Link>
      {query.isPending && <Loading>Opening entry…</Loading>}
      <ErrorNotice error={query.error} prefix="Could not open this entry" />
      {entity && (
        <>
          <header className="entry-head">
            <TypeAvatar type={entity.type} name={entity.name} size="lg" />
            <div className="titles">
              <h1>{entity.name}</h1>
              <div className="actions" style={{ marginTop: '0.4rem' }}>
                <Badge tone="accent">{typeLabel(entity.type)}</Badge>
                {!active && <Badge tone="danger">Archived</Badge>}
                {(entity.aliases ?? []).length > 0 && (
                  <span className="muted">also known as {(entity.aliases ?? []).join(', ')}</span>
                )}
              </div>
            </div>
            {editable && (
              <div className="actions">
                <Button icon="edit" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <ArchiveEntry entity={entity} />
              </div>
            )}
          </header>

          <div className="stack" style={{ marginBottom: '1.25rem' }} hidden={canWrite && Boolean(active)}>
            <ReadOnlyNotice canWrite={canWrite} />
            {!active && (
              <p className="notice plain" role="note">
                This entry is archived. It can no longer be changed, and its history is kept.
              </p>
            )}
          </div>

          <div className="two-col">
            <div className="section-gap">
              <Details entity={entity} onEdit={editable ? () => setEditing(true) : undefined} />
              <FactsPanel entity={entity} />
              {entity.type === 'Character' && <Beliefs entity={entity} />}
              {entity.type === 'Character' && <Events entity={entity} />}
              {entity.type === 'Character' && editable && <Interview entity={entity} />}
            </div>
            <div className="section-gap">
              <MediaLinksPanel targetId={entity.id} />
              <ActivityPanel recordType="entity" recordId={entity.id} />
            </div>
          </div>

          {editing && (
            <Dialog title={`Edit ${entity.name}`} onClose={() => setEditing(false)} wide>
              <EntityForm entity={entity} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />
            </Dialog>
          )}
        </>
      )}
    </>
  )
}
