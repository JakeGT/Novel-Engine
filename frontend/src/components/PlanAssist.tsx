import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type AssistSession, type PlanItem } from '../api/client'
import { planKindLabel, plural } from '../lib/words'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import { AllowanceNote } from './Allowance'
import { AssistantProblem } from './AssistFlow'
import { Button, Dialog, Field } from './ui'

type Sheet = { session_id: string; questions: { id: string; question: string }[] }

const DEPTHS: { value: string; label: string; hint: string }[] = [
  { value: 'arcs', label: 'The premise and the main arcs', hint: 'The shape of the story, and whose it is.' },
  { value: 'beats', label: 'Arcs and their turning points', hint: 'The beats each arc moves through.' },
  { value: 'scenes', label: 'Down to scenes', hint: 'Where each beat happens, as far as you have decided.' },
]

/**
 * Plan part of the story by talking it through. The author says what they mean to
 * happen and how far to go; the assistant may ask a few questions, then drafts
 * plan items for review. It writes down what was said and leaves the rest open.
 */
export function PlanAssist({
  about,
  resume,
  onClose,
  onManual,
}: {
  /** The part of the plan being worked on, if any. */
  about?: PlanItem
  /** An unfinished planning conversation to pick up. */
  resume?: AssistSession
  onClose: () => void
  /** Go to the by-hand form instead, one click away. */
  onManual?: () => void
}) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [description, setDescription] = useState(resume?.description ?? '')
  const [depth, setDepth] = useState(about?.kind === 'beat' ? 'scenes' : about?.kind === 'arc' ? 'beats' : 'arcs')
  const [sheet, setSheet] = useState<Sheet | null>(
    resume && resume.questions.length > 0 ? { session_id: resume.id, questions: resume.questions } : null,
  )
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries((resume?.answers ?? []).map((item) => [item.question_id, item.answer])),
  )
  const [problem, setProblem] = useState<string | null>(null)
  const brief = () => ({ description: description.trim(), depth, about_id: about?.id ?? null })
  const answered = () =>
    (sheet?.questions ?? [])
      .filter((question) => (answers[question.id] ?? '').trim())
      .map((question) => ({ question_id: question.id, question: question.question, answer: answers[question.id].trim() }))

  const ask = useMutation({
    mutationFn: () => api.planQuestions(scope, brief()),
    onSuccess: (data) => {
      setSheet(data)
      void queryClient.invalidateQueries({ queryKey: scopedKey(scope, 'assist-sessions') })
    },
  })
  const draft = useMutation({
    mutationFn: async () => {
      // A proposal is not a canon write: aim it at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.planProposal(scope, {
        ...brief(),
        session_id: sheet?.session_id ?? null,
        answers: answered(),
        expected_revision: head,
      })
    },
    onSuccess: async (result) => {
      await invalidate()
      onClose()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: { fromAssistant: true, understood: result.understood, notes: result.notes, discarded: result.discarded },
      })
    },
  })

  const ready = () => {
    if (!description.trim() && answered().length === 0 && !about) {
      setProblem('Say something about the story first.')
      return false
    }
    setProblem(null)
    return true
  }
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (ready()) draft.mutate()
  }
  const busy = ask.isPending || draft.isPending

  return (
    <Dialog title={about ? `Plan what goes under “${about.title}”` : 'Plan your story'} onClose={onClose} wide>
      <form className="stack" onSubmit={onSubmit} noValidate aria-label="Plan with the assistant">
        <p className="muted">
          Say what you mean to happen, in your own words. The assistant writes down what you
          tell it as a plan for you to review, and leaves open whatever you have not decided. It
          does not make the story up for you.
        </p>
        <Field
          label={about ? `What happens in this ${planKindLabel(about.kind).toLowerCase()}? (optional)` : 'What do you know so far?'}
          hint="A sentence or a page. What is at stake, whose story it is, what turns."
          error={problem}
        >
          {(props) => (
            <textarea
              {...props}
              data-autofocus
              rows={5}
              value={description}
              disabled={busy}
              onChange={(event) => setDescription(event.target.value)}
            />
          )}
        </Field>

        <fieldset>
          <legend>How far to plan this time</legend>
          <div className="stack tight">
            {DEPTHS.map((item) => (
              <label key={item.value} className="check">
                <input
                  type="radio"
                  name="plan-depth"
                  value={item.value}
                  checked={depth === item.value}
                  disabled={busy}
                  onChange={() => setDepth(item.value)}
                />
                <span>
                  {item.label} <span className="muted small">— {item.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {sheet && sheet.questions.length > 0 && (
          <fieldset className="stack">
            <legend>{plural(sheet.questions.length, 'question')} from the assistant</legend>
            <p className="hint">Answer as many or as few as you like. Leave blank what you have not decided.</p>
            {sheet.questions.map((question) => (
              <div className="field" key={question.id}>
                <label htmlFor={`plan-${question.id}`}>{question.question}</label>
                <textarea
                  id={`plan-${question.id}`}
                  rows={2}
                  value={answers[question.id] ?? ''}
                  disabled={busy}
                  onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })}
                />
              </div>
            ))}
          </fieldset>
        )}

        <AllowanceNote reads="story" />
        <AssistantProblem error={ask.error ?? draft.error} onManual={onManual} />
        <div className="actions end">
          {onManual && (
            <Button variant="ghost" onClick={onManual}>
              Add to the plan by hand
            </Button>
          )}
          {!sheet && (
            <Button icon="chat" disabled={busy} onClick={() => ready() && ask.mutate()}>
              {ask.isPending ? 'Thinking of questions…' : 'Ask me questions first'}
            </Button>
          )}
          <Button variant="primary" type="submit" icon="spark" disabled={busy}>
            {draft.isPending ? 'Drafting the plan…' : 'Draft the plan'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/**
 * "What could happen next?" Two or three directions the story could take, each
 * with what it would change. The author picks one to review, or none.
 */
export function NextDirections({ about, onClose }: { about?: PlanItem; onClose: () => void }) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const offer = useMutation({ mutationFn: () => api.planDirections(scope, about?.id ?? null) })
  const choose = useMutation({
    mutationFn: async (draft: Parameters<typeof api.chooseDirection>[1]) => {
      const head = await refreshHead().catch(() => headRevision)
      return api.chooseDirection(scope, draft, head)
    },
    onSuccess: async (result) => {
      await invalidate()
      onClose()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: { fromAssistant: true, understood: result.understood, notes: result.notes, discarded: result.discarded },
      })
    },
  })
  const directions = offer.data?.directions ?? []

  return (
    <Dialog title="What could happen next?" onClose={onClose} wide>
      <div className="stack">
        <p className="muted">
          The assistant reads your story bible and your plan and offers a few ways the story
          could go{about ? ` from “${about.title}”` : ''}. They are suggestions to choose from, change or
          ignore. Picking one shows you what it would add to the plan before anything is saved.
        </p>
        {!offer.data && (
          <>
            <AllowanceNote reads="story" />
            <AssistantProblem error={offer.error} />
            <div className="actions">
              <Button variant="primary" icon="spark" disabled={offer.isPending} onClick={() => offer.mutate()}>
                {offer.isPending ? 'Reading your story…' : 'Show me some directions'}
              </Button>
            </div>
          </>
        )}
        {directions.length > 0 && (
          <ol className="directions">
            {directions.map((direction, index) => (
              <li key={index} className="card quiet">
                <h3>{direction.title}</h3>
                <p>{direction.what_happens}</p>
                {direction.what_it_changes.length > 0 && (
                  <>
                    <p className="label">What it would change</p>
                    <ul>
                      {direction.what_it_changes.map((change, position) => (
                        <li key={position}>{change}</li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="actions">
                  <Button
                    disabled={choose.isPending}
                    aria-label={`Plan this direction: ${direction.title}`}
                    onClick={() => choose.mutate(direction.draft)}
                  >
                    Plan this one
                  </Button>
                  <span className="hint">Suggests {plural(direction.adds, 'change')} for you to review.</span>
                </div>
              </li>
            ))}
          </ol>
        )}
        <AssistantProblem error={choose.error} />
        {offer.data && (
          <div className="actions end">
            <Button variant="ghost" onClick={onClose}>
              None of these
            </Button>
            <Button disabled={offer.isPending} onClick={() => offer.mutate()}>
              {offer.isPending ? 'Reading your story…' : 'Offer different ones'}
            </Button>
          </div>
        )}
      </div>
    </Dialog>
  )
}
