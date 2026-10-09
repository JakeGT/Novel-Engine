import { useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  isApiError,
  type Belief,
  type CharacterQuestionSheet,
} from '../api/client'
import { MediaLinksPanel } from '../components/MediaLinksPanel'
import { ProvenancePanel } from '../components/ProvenancePanel'
import { RelationshipsPanel } from '../components/RelationshipsPanel'
import { Badge, ErrorNotice } from '../components/ui'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import { EntitySummary } from './EntityDetail'

const AGREEMENT: Record<string, { label: string; tone: 'ok' | 'danger' | 'neutral' }> = {
  agrees: { label: 'Matches canon', tone: 'ok' },
  disagrees: { label: 'Contradicts canon', tone: 'danger' },
  unknown: { label: 'Canon is silent', tone: 'neutral' },
}

function beliefText(belief: Belief, names: Map<string, string>): string {
  const object = belief.object as Record<string, unknown>
  const value =
    object.kind === 'entity_ref'
      ? (names.get(String(object.entity_id)) ?? String(object.entity_id))
      : String(object.value)
  return `${names.get(belief.subject_id) ?? belief.subject_id} ${belief.predicate} → ${value}`
}

function ProviderProblem({ error }: { error: unknown }) {
  if (!error) return null
  if (isApiError(error, 'capability_unavailable') || isApiError(error, 'provider_failure')) {
    return (
      <p className="notice warn" role="alert">
        The guided interview needs an AI provider, and none is available for this workspace right
        now. Nothing was changed. You can still edit this character, its facts, and its beliefs by
        hand.
      </p>
    )
  }
  return <ErrorNotice error={error} />
}

function GuidedWorkflow({ entityId }: { entityId: string }) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const [sheet, setSheet] = useState<CharacterQuestionSheet | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})

  const questions = useMutation({
    mutationFn: () => api.characterQuestions(scope, entityId),
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
      // A proposal is not a canon write: target whatever the head is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.characterProposal(scope, entityId, { answers: answered, expected_revision: head })
    },
    onSuccess: async (result) => {
      await invalidate()
      navigate(`/projects/${scope.projectId}/mutations/${result.mutation_id}`, {
        state: { fromGuidedWorkflow: true, discarded: result.discarded },
      })
    },
  })
  const anyAnswer = Object.values(answers).some((answer) => answer.trim())

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    proposal.mutate()
  }

  return (
    <section className="panel" aria-labelledby="guided-heading">
      <h3 id="guided-heading">Guided interview</h3>
      <p className="muted">
        The assistant asks questions and turns your answers into a <strong>proposal</strong>.
        Nothing reaches canon until you review and commit that proposal.
      </p>
      {!sheet && (
        <button
          type="button"
          className="button"
          onClick={() => questions.mutate()}
          disabled={questions.isPending}
        >
          {questions.isPending ? 'Preparing questions…' : 'Generate questions'}
        </button>
      )}
      <ProviderProblem error={questions.error} />
      {sheet && (
        <form className="stack" onSubmit={onSubmit} aria-label="Guided interview answers">
          {sheet.questions.map((question) => (
            <label key={question.id}>
              <span>
                {question.question} <span className="muted">({question.category})</span>
              </span>
              <textarea
                rows={2}
                value={answers[question.id] ?? ''}
                onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })}
              />
            </label>
          ))}
          <button className="button" type="submit" disabled={!anyAnswer || proposal.isPending}>
            {proposal.isPending ? 'Building proposal…' : 'Create proposal for review'}
          </button>
          <ProviderProblem error={proposal.error} />
        </form>
      )}
    </section>
  )
}

export function CharacterView() {
  const { scope, canWrite } = useProject()
  const { entityId = '' } = useParams()

  const entity = useQuery({
    queryKey: scopedKey(scope, 'entity', entityId),
    queryFn: () => api.getEntity(scope, entityId),
  })
  const beliefs = useQuery({
    queryKey: scopedKey(scope, 'beliefs', entityId),
    queryFn: () => api.listBeliefs(scope, { holder_entity_id: entityId, limit: 200 }),
  })
  const events = useQuery({
    queryKey: scopedKey(scope, 'events', 'entity', entityId),
    queryFn: () => api.listEvents(scope, { entity_id: entityId, limit: 200 }),
  })
  const entities = useQuery({
    queryKey: scopedKey(scope, 'entities', '', '', true),
    queryFn: () => api.listEntities(scope, { include_retired: true, limit: 200 }),
  })
  const beliefItems = beliefs.data?.items ?? []
  const agreements = useQueries({
    queries: beliefItems.map((belief) => ({
      queryKey: scopedKey(scope, 'truth-agreement', belief.id),
      queryFn: () => api.truthAgreement(scope, belief.id),
    })),
  })

  const names = new Map((entities.data?.items ?? []).map((item) => [item.id, item.name]))
  const participating = (events.data?.items ?? []).filter((event) =>
    event.participants.some((participant) => participant.entity_id === entityId),
  )

  return (
    <>
      <p>
        <Link to={`/projects/${scope.projectId}`}>← Entities</Link>
      </p>
      {entity.isLoading && (
        <p className="muted" role="status">
          Loading character…
        </p>
      )}
      <ErrorNotice error={entity.error} />
      {entity.data && entity.data.type !== 'Character' && (
        <p className="notice" role="note">
          This entity is a {entity.data.type}, not a character.{' '}
          <Link to={`/projects/${scope.projectId}/entities/${entityId}`}>Open the entity view</Link>
        </p>
      )}
      {entity.data && entity.data.type === 'Character' && (
        <>
          <EntitySummary entity={entity.data} />
          <RelationshipsPanel entity={entity.data} />

          <section className="panel" aria-labelledby="beliefs-heading">
            <h3 id="beliefs-heading">Beliefs</h3>
            <p className="muted">
              What {entity.data.name} thinks is true. “Compared with canon” is worked out from the
              current facts each time you look; it is never stored with the belief.
            </p>
            <ErrorNotice error={beliefs.error} prefix="Could not load beliefs" />
            {beliefItems.length === 0 && !beliefs.isLoading && (
              <p className="muted">No open beliefs.</p>
            )}
            <ul className="list">
              {beliefItems.map((belief, index) => {
                const agreement = agreements[index]?.data?.truth_agreement
                const view = agreement ? AGREEMENT[agreement] : undefined
                return (
                  <li key={belief.id}>
                    {beliefText(belief, names)}{' '}
                    <span className="muted">confidence {belief.confidence}</span>{' '}
                    {view ? (
                      <Badge tone={view.tone}>Compared with canon: {view.label}</Badge>
                    ) : (
                      <span className="muted">comparing with canon…</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="panel" aria-labelledby="events-heading">
            <h3 id="events-heading">Events</h3>
            {participating.length === 0 && !events.isLoading && (
              <p className="muted">Not recorded as a participant in any event.</p>
            )}
            <ul className="list">
              {participating.map((event) => (
                <li key={event.id}>
                  <strong>{event.name}</strong> <span className="muted">{event.kind}</span> — as{' '}
                  {event.participants
                    .filter((participant) => participant.entity_id === entityId)
                    .map((participant) => participant.role)
                    .join(', ')}
                  {event.world_position ? (
                    <span className="muted"> · {event.world_position}</span>
                  ) : (
                    <span className="muted"> · date unknown</span>
                  )}
                </li>
              ))}
            </ul>
          </section>

          {canWrite && entity.data.lifecycle === 'active' && <GuidedWorkflow entityId={entityId} />}
          <MediaLinksPanel targetId={entityId} />
          <ProvenancePanel recordType="entity" recordId={entityId} />
        </>
      )}
    </>
  )
}
