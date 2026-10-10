import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type AssistSession, type SchemaSuggestion } from '../api/client'
import { predicateLabel, typeLabel } from '../lib/words'
import { timeAgo } from '../lib/time'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import { AssistantProblem } from './AssistFlow'
import { Button, ConfirmDialog, ErrorNotice } from './ui'

const KIND: Record<string, string> = {
  create: 'Adding to the story bible',
  fill_in: 'Filling in an entry',
  change: 'Changing something',
}

/** The author's own words, shortened to fit a row. */
function excerpt(text: string, length = 140): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > length ? `${flat.slice(0, length).trimEnd()}…` : flat
}

function title(session: AssistSession): string {
  if (session.description.trim()) return `“${excerpt(session.description)}”`
  return session.name ? `About ${session.name}` : 'A conversation with the assistant'
}

/**
 * Conversations with the assistant that were left unfinished, offered where the
 * author starts work, so nothing has to be remembered or typed again.
 */
export function OpenConversations() {
  const { scope, canWrite, resume } = useProject()
  const queryClient = useQueryClient()
  const key = scopedKey(scope, 'assist-sessions')
  const waiting = useQuery({
    queryKey: key,
    queryFn: () => api.openAssistSessions(scope),
    enabled: canWrite,
  })
  const [putting, setPutting] = useState<AssistSession | null>(null)
  const putAway = useMutation({
    mutationFn: (session: AssistSession) => api.closeAssistSession(scope, session.id),
    onSuccess: async () => {
      setPutting(null)
      await queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const items = waiting.data?.items ?? []
  if (!canWrite || items.length === 0) return null

  return (
    <section className="card" aria-labelledby="unfinished-heading">
      <div className="card-head">
        <div>
          <h2 id="unfinished-heading">Pick up where you left off</h2>
          <p className="muted">
            Conversations with the assistant you started and did not finish. Only you can see
            these.
          </p>
        </div>
      </div>
      <ul className="rows">
        {items.map((session) => {
          const asked = session.questions.length
          return (
            <li className="row" key={session.id}>
              <span className="grow">
                {title(session)}
                <span className="muted small" style={{ display: 'block' }}>
                  {KIND[session.kind] ?? 'A conversation'}
                  {asked > 0 &&
                    (session.kind === 'change'
                      ? ' · the assistant asked you something'
                      : ` · ${session.answers.length} of ${asked} questions answered`)}
                  {' · '}
                  {timeAgo(session.updated_at)}
                </span>
              </span>
              <Button size="sm" icon="chat" onClick={() => resume?.(session)}>
                Continue
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setPutting(session)}>
                Put away
              </Button>
            </li>
          )
        })}
      </ul>
      {putting && (
        <ConfirmDialog
          title="Put this conversation away?"
          confirmLabel="Put it away"
          keepLabel="Keep it here"
          busy={putAway.isPending}
          error={putAway.error}
          onCancel={() => {
            putAway.reset()
            setPutting(null)
          }}
          onConfirm={() => putAway.mutate(putting)}
        >
          <p>{title(putting)}</p>
          <p>
            What you typed in it is erased and cannot be brought back. Nothing in your story
            changes.
          </p>
        </ConfirmDialog>
      )}
    </section>
  )
}

/**
 * What the author told the assistant that led to a set of suggested changes:
 * their own words, quoted back, so a reviewer can judge the changes against them.
 */
export function ConversationEvidence({
  mutationId,
  wordsOnly = false,
}: {
  mutationId: string
  /** Leave out the assistant's summary when the page has just shown it. */
  wordsOnly?: boolean
}) {
  const { scope } = useProject()
  const found = useQuery({
    queryKey: scopedKey(scope, 'assist-session-for', mutationId),
    queryFn: () => api.assistSessionFor(scope, mutationId),
  })
  if (found.error) return <ErrorNotice error={found.error} prefix="Could not load where this came from" />
  const session = found.data
  if (!session) return null

  return (
    <>
    {session.suggestions.length > 0 && <SetupSuggestions session={session} />}
    <section className="card quiet" aria-labelledby="evidence-heading">
      <div className="card-head">
        <div>
          <h2 id="evidence-heading">Where this came from</h2>
          <p className="muted">
            What the assistant was told, {timeAgo(session.created_at)}. The changes below should
            say nothing more than this.
          </p>
        </div>
      </div>
      <div className="stack tight">
        {session.description.trim() && (
          <div>
            <p className="label">{session.kind === 'change' ? 'The instruction' : 'The description'}</p>
            <blockquote className="quoted">{session.description}</blockquote>
          </div>
        )}
        {session.answers.length > 0 && (
          <div>
            <p className="label">Questions and answers</p>
            <dl className="qa">
              {session.answers.map((item) => (
                <div key={item.question_id}>
                  <dt>{item.question}</dt>
                  <dd>{item.answer}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        {!wordsOnly && session.understood && (
          <div>
            <p className="label">How the assistant understood it</p>
            <p>{session.understood}</p>
          </div>
        )}
        {!wordsOnly && session.notes.length > 0 && (
          <div>
            <p className="label">Worth knowing before you decide</p>
            <ul>
              {session.notes.map((item, index) => (
                <li key={index}>• {item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
    </>
  )
}

const POINTS_TO: Record<string, string> = {
  text: 'a piece of text',
  whole_number: 'a whole number',
  yes_no: 'yes or no',
}

const list = (types: string[]) => types.map((type) => typeLabel(type)).join(' or ')

/** "Connects a Character to a piece of text." */
function meaning(item: SchemaSuggestion): string {
  if (item.kind === 'type') return item.description || 'A new type of entry.'
  const target = item.points_to === 'entry' ? `a ${list(item.object_types)}` : POINTS_TO[item.points_to]
  return `Connects a ${list(item.subject_types)} to ${target}${item.one_at_a_time ? ', one at a time' : ''}.${
    item.description ? ` ${item.description}` : ''
  }`
}

const suggestionTitle = (item: SchemaSuggestion) =>
  item.kind === 'type'
    ? `A type of entry: ${typeLabel(item.name)}`
    : `A kind of fact: “${predicateLabel(item.name)}”`

/**
 * What the assistant could not express with the project's types and kinds of
 * fact. Nothing is added unless an owner chooses to; after adding, the same
 * description can be drafted again so what was left out can be included.
 */
function SetupSuggestions({ session }: { session: AssistSession }) {
  const { scope, canWrite, canAdmin, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const have = new Set([
    ...(vocabulary.data?.entity_types ?? []).map((item) => item.name),
    ...(vocabulary.data?.predicates ?? []).map((item) => item.predicate),
  ])
  const missing = session.suggestions.filter((item) => !have.has(item.name))
  const [skipped, setSkipped] = useState<Set<string>>(new Set())
  const chosen = missing.filter((item) => !skipped.has(item.name))
  // A kind of fact cannot be added without a suggested type it connects.
  const needed = new Set(
    chosen.flatMap((item) => [...item.subject_types, ...item.object_types]).filter((type) => !have.has(type)),
  )
  const orphaned = [...needed].filter((type) => !chosen.some((item) => item.name === type))

  const add = useMutation({
    mutationFn: () => api.applySuggestions(scope, session.id, chosen.map((item) => item.name)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: scopedKey(scope, 'vocabulary') })
      await invalidate()
    },
  })
  const redraft = useMutation({
    mutationFn: async () => {
      // Aim the new draft at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      if (session.kind === 'change') {
        return api.assistChange(scope, {
          instruction: session.description,
          entity_id: session.entity_id,
          session_id: null,
          expected_revision: head,
        })
      }
      return api.assistProposal(scope, {
        description: session.description,
        entity_type: session.entity_id ? null : session.entity_type,
        name: session.entity_id ? null : session.name,
        entity_id: session.entity_id,
        session_id: null,
        answers: session.answers,
        expected_revision: head,
      })
    },
    onSuccess: async (result) => {
      await invalidate()
      if (result.mutation_id) {
        navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
          state: { fromAssistant: true, discarded: result.discarded, redrafted: true },
        })
      }
    },
  })
  const anyAdded = session.suggestions.some((item) => have.has(item.name))

  return (
    <section className="card" aria-labelledby="setup-heading">
      <div className="card-head">
        <div>
          <h2 id="setup-heading">Suggested additions to your setup</h2>
          <p className="muted">
            You said things your story bible has no way to record yet, so the assistant left them
            out rather than force them into the wrong shape. Adding these lets it record them.
            Nothing is added unless you choose to.
          </p>
        </div>
      </div>
      <ul className="rows">
        {session.suggestions.map((item) => {
          const added = have.has(item.name)
          const id = `suggestion-${item.name}`
          return (
            <li className="row" key={item.name} style={{ alignItems: 'flex-start' }}>
              {canAdmin && !added && (
                <input
                  id={id}
                  type="checkbox"
                  checked={!skipped.has(item.name)}
                  onChange={(event) => {
                    const next = new Set(skipped)
                    if (event.target.checked) next.delete(item.name)
                    else next.add(item.name)
                    setSkipped(next)
                  }}
                />
              )}
              <span className="grow">
                <label htmlFor={canAdmin && !added ? id : undefined}>
                  <strong>{suggestionTitle(item)}</strong>
                </label>
                <span style={{ display: 'block' }}>{meaning(item)}</span>
                {item.reason && (
                  <span className="muted small" style={{ display: 'block' }}>
                    Why: {item.reason}
                  </span>
                )}
              </span>
              {added && <span className="badge ok">In your setup</span>}
            </li>
          )
        })}
      </ul>
      {!canAdmin && missing.length > 0 && (
        <p className="notice plain" role="note">
          Only a project owner can add to the setup. Ask one to open this page.
        </p>
      )}
      {orphaned.length > 0 && (
        <p className="hint">
          Keep {list(orphaned)} ticked as well: a kind of fact you have ticked connects to it.
        </p>
      )}
      <ErrorNotice error={add.error} prefix="Nothing was added to your setup" />
      <AssistantProblem error={redraft.error} />
      {redraft.isSuccess && !redraft.data.mutation_id && (
        <p className="notice plain" role="status">
          The assistant asked a question instead of drafting. Use “Change anything” to answer it.
        </p>
      )}
      <div className="actions">
        {canAdmin && missing.length > 0 && (
          <Button
            variant="primary"
            icon="plus"
            disabled={add.isPending || chosen.length === 0 || orphaned.length > 0}
            onClick={() => add.mutate()}
          >
            {add.isPending ? 'Adding…' : `Add ${chosen.length === 1 ? 'this' : `these ${chosen.length}`} to my setup`}
          </Button>
        )}
        {anyAdded && canWrite && (
          <>
            <Button icon="spark" disabled={redraft.isPending} onClick={() => redraft.mutate()}>
              {redraft.isPending ? 'Drafting…' : 'Draft again using them'}
            </Button>
            <span className="hint">
              Makes a new set of suggested changes from the same words. This one stays in your
              review inbox until you add or discard it.
            </span>
          </>
        )}
      </div>
    </section>
  )
}
