import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type AssistSession } from '../api/client'
import { timeAgo } from '../lib/time'
import { scopedKey, useProject } from '../project'
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
  )
}
