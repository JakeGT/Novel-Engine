import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type AssistThread, type ThreadKind, type ThreadTurn } from '../api/client'
import { useKeptThread } from '../lib/threads'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, ErrorNotice } from './ui'

const RECENT = 4

function Turn({ turn, onLeave }: { turn: ThreadTurn; onLeave?: () => void }) {
  const { scope } = useProject()
  if (turn.role === 'author') {
    return (
      <li className="turn author">
        <span className="who">You</span>
        {turn.body && <p>{turn.body}</p>}
        {turn.answers.length > 0 && (
          <ul>
            {turn.answers.map((item, index) => (
              <li key={index}>
                <span className="muted">{item.question}</span> {item.answer}
              </li>
            ))}
          </ul>
        )}
      </li>
    )
  }
  return (
    <li className="turn assistant">
      <span className="who">Assistant</span>
      {turn.body && <p>{turn.body}</p>}
      {turn.questions.length > 0 && (
        <ul>
          {turn.questions.map((question, index) => (
            <li key={index}>{question}</li>
          ))}
        </ul>
      )}
      {turn.mutation_id && (
        <p className="small">
          Suggested {plural(turn.operation_count, 'change')}.{' '}
          <Link to={`/projects/${scope.projectId}/review/${turn.mutation_id}`} onClick={onLeave}>
            Open {turn.operation_count === 1 ? 'it' : 'them'} in review
          </Link>
        </p>
      )}
    </li>
  )
}

/**
 * What was said before in this conversation, newest last, with a way to clear it and start
 * again. Clearing erases the words; suggested and saved changes stay where they are.
 */
export function KeptThread({
  kind,
  onLeave,
  onCleared,
}: {
  kind: ThreadKind
  onLeave?: () => void
  onCleared?: () => void
}) {
  const { scope, canWrite } = useProject()
  const queryClient = useQueryClient()
  const thread = useKeptThread(kind)
  const [all, setAll] = useState(false)
  const [asking, setAsking] = useState(false)
  const clear = useMutation({
    mutationFn: () => api.clearAssistThread(scope, kind),
    onSuccess: () => {
      setAsking(false)
      queryClient.setQueryData<AssistThread>(scopedKey(scope, 'assist-thread', kind), {
        kind,
        id: null,
        turns: [],
        open_questions: [],
        tokens_used: 0,
        updated_at: null,
      })
      onCleared?.()
    },
  })
  const turns = thread.data?.turns ?? []
  if (turns.length === 0) return null
  const shown = all ? turns : turns.slice(-RECENT)
  return (
    <section className="stack tight" aria-labelledby={`thread-${kind}`}>
      <div className="thread-head">
        <h3 id={`thread-${kind}`} className="label">
          Earlier in this conversation
        </h3>
        {turns.length > shown.length && (
          <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
            Show the whole conversation ({plural(turns.length, 'turn')})
          </Button>
        )}
      </div>
      <ol className="thread">
        {shown.map((turn) => (
          <Turn key={turn.position} turn={turn} onLeave={onLeave} />
        ))}
      </ol>
      <p className="hint">
        The assistant reads the last few turns again each time, so it remembers what you meant.
      </p>
      {canWrite && !asking && (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setAsking(true)}>
            Clear and start again
          </Button>
        </div>
      )}
      {asking && (
        <div className="notice plain" role="group" aria-label="Clear this conversation?">
          <p>
            <strong>Clear this conversation?</strong> What you and the assistant said is erased
            and cannot be brought back. Changes it suggested stay in review, and saved ones stay
            in your story.
          </p>
          <div className="actions">
            <Button data-autofocus onClick={() => setAsking(false)}>
              Keep the conversation
            </Button>
            <Button variant="danger-quiet" disabled={clear.isPending} onClick={() => clear.mutate()}>
              {clear.isPending ? 'Clearing…' : 'Clear conversation'}
            </Button>
          </div>
          <ErrorNotice error={clear.error} prefix="Could not clear the conversation" />
        </div>
      )}
    </section>
  )
}
