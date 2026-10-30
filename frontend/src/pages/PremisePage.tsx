import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, type PremiseRound } from '../api/client'
import { AllowanceNote } from '../components/Allowance'
import { AssistantProblem } from '../components/AssistFlow'
import { Icon } from '../components/icons'
import { KeptThread } from '../components/KeptThread'
import { Button, ErrorNotice, Field, Loading, ReadOnlyNotice } from '../components/ui'
import { useKeptThread } from '../lib/threads'
import { plural } from '../lib/words'
import { scopedKey, useInvalidateProject, useProject } from '../project'

const EXAMPLE =
  'Wren Hollis keeps the books at The Old Mill in Saltmere. When her brother goes missing, she finds his name in a smuggler’s ledger and starts asking the wrong people questions.'

/**
 * Start from a premise (AST-EXP-005): the author describes the story; the assistant suggests
 * the people, places and links it finds, for review, and asks what it still needs to know.
 * Answers go round again. Nothing is added to the story until the author approves it.
 */
export function PremisePage() {
  const { scope, canWrite, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const queryClient = useQueryClient()
  const thread = useKeptThread('premise')
  const [premise, setPremise] = useState('')
  const [more, setMore] = useState('')
  // Answers belong to the questions they answer: a new round's questions start empty.
  const [typed, setTyped] = useState<{ round: string; values: Record<string, string> }>({
    round: '',
    values: {},
  })
  const [last, setLast] = useState<PremiseRound | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const started = (thread.data?.turns.length ?? 0) > 0
  const open = thread.data?.open_questions ?? []
  const round = open.join('\n')
  const answers = typed.round === round ? typed.values : {}
  const answer = (question: string, value: string) =>
    setTyped({ round, values: { ...answers, [question]: value } })

  const send = useMutation({
    mutationFn: async () => {
      const head = await refreshHead().catch(() => headRevision)
      return api.premiseRound(scope, {
        premise: started ? more.trim() : premise.trim(),
        answers: open.map((question) => ({ question, answer: (answers[question] ?? '').trim() })),
        expected_revision: head,
      })
    },
    onSuccess: async (result) => {
      setLast(result)
      setPremise('')
      setMore('')
      await queryClient.invalidateQueries({ queryKey: scopedKey(scope, 'assist-thread', 'premise') })
      if (result.mutation_id) await invalidate()
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!started && !premise.trim()) return setProblem('Describe your story first.')
    const answered = open.some((question) => (answers[question] ?? '').trim())
    if (started && !answered && !more.trim()) {
      return setProblem('Answer at least one question, or add more about your story.')
    }
    setProblem(null)
    send.mutate()
  }

  return (
    <div className="section-gap">
      <header className="page-head">
        <div>
          <h1>Start from your story</h1>
          <p className="lede">
            Tell the assistant about your story in your own words. It suggests the people, places
            and links it finds, for you to review, and asks what it still needs to know. Answer what
            you like, skip the rest, and go round again.
          </p>
        </div>
      </header>

      <ReadOnlyNotice canWrite={canWrite} />
      {thread.isPending && <Loading>Opening your conversation…</Loading>}
      <ErrorNotice error={thread.error} prefix="Could not open your conversation" />

      {last && (
        <section className="card" aria-labelledby="round-heading" role="status">
          <div className="card-head">
            <h2 id="round-heading">
              {last.mutation_id
                ? `${plural(last.operation_count, 'suggested change')} to review`
                : 'Nothing new to add yet'}
            </h2>
          </div>
          {last.understood && <p>{last.understood}</p>}
          {last.mutation_id ? (
            <div className="stack tight">
              <p className="muted small">Nothing is added to your story until you approve it.</p>
              <div>
                {/* One focal point: while questions are open, answering them is the main step. */}
                <Link
                  className={open.length > 0 ? 'btn' : 'btn primary'}
                  to={`/projects/${scope.projectId}/review/${last.mutation_id}`}
                >
                  <Icon name="inbox" size={17} /> Review the suggestions
                </Link>
              </div>
            </div>
          ) : (
            <p className="muted small">The assistant only asked questions this time.</p>
          )}
          {last.discarded.length > 0 && (
            <details className="more">
              <summary>Left out ({plural(last.discarded.length, 'item')})</summary>
              <ul className="plain-list small">
                {last.discarded.map((item, index) => (
                  <li key={index}>{item.reason}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {started && (
        <section className="card quiet">
          <KeptThread kind="premise" onCleared={() => setLast(null)} />
        </section>
      )}

      {canWrite && thread.isSuccess && (
        <form className="card stack" onSubmit={submit} noValidate aria-label="Start from your story">
          {!started ? (
            <>
              <Field
                label="Your story, in your own words"
                hint="A paragraph is plenty: who it is about, where it happens, what goes wrong."
                error={problem}
              >
                {(props) => (
                  <textarea
                    {...props}
                    data-autofocus
                    rows={7}
                    value={premise}
                    onChange={(event) => setPremise(event.target.value)}
                  />
                )}
              </Field>
              {!premise && (
                <div className="chips" role="group" aria-label="Example">
                  <button type="button" className="chip" onClick={() => setPremise(EXAMPLE)}>
                    Use an example
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              {open.length > 0 ? (
                <fieldset className="premise-questions">
                  <legend className="label">The assistant would like to know</legend>
                  {open.map((question) => (
                    <Field key={question} label={question} hint="Leave it empty to skip this one.">
                      {(props) => (
                        <textarea
                          {...props}
                          rows={2}
                          value={answers[question] ?? ''}
                          onChange={(event) => answer(question, event.target.value)}
                        />
                      )}
                    </Field>
                  ))}
                </fieldset>
              ) : (
                <p className="muted">
                  The assistant has no more questions. Add anything else about your story below, or
                  carry on in the story bible.
                </p>
              )}
              <Field label="Anything else to add about your story? (optional)" error={problem}>
                {(props) => (
                  <textarea {...props} rows={3} value={more} onChange={(event) => setMore(event.target.value)} />
                )}
              </Field>
            </>
          )}
          <AllowanceNote reads="entry" />
          <AssistantProblem error={send.error} />
          <div className="actions end">
            <Link className="btn ghost" to={`/projects/${scope.projectId}/bible`}>
              Go to the story bible
            </Link>
            <Button variant="primary" type="submit" icon="spark" disabled={send.isPending}>
              {send.isPending ? 'Reading your story…' : started ? 'Send my answers' : 'Read it and ask me'}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
