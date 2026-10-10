import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  api,
  isApiError,
  type AssistBrief,
  type AssistQuestionSheet,
  type CanonEntity,
} from '../api/client'
import { COMMON_TYPES, typeLabel, typeLook } from '../lib/words'
import { useInvalidateProject, useProject } from '../project'
import { Icon } from './icons'
import { Button, ErrorNotice, Field } from './ui'

interface Props {
  /** Fill in this existing entry instead of adding something new. */
  entity?: CanonEntity
  initialType?: string
  /** The entry types the project offers. */
  types: string[]
  onClose: () => void
  /** Switch to typing everything in by hand, keeping what was entered so far. */
  onManual?: (draft: { type?: string; name: string }) => void
}

const EXAMPLE =
  'Mara Venn is a harbor pilot in Saltmere. She belongs to the Harbor Guild, owns a brass sextant, and doesn’t trust Tobin Ashe.'

export function AssistantProblem({ error, onManual }: { error: unknown; onManual?: () => void }) {
  const { scope, access } = useProject()
  if (!error) return null
  if (isApiError(error, 'capability_unavailable') || isApiError(error, 'provider_failure')) {
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>The assistant isn’t available right now.</strong> It needs an AI provider, and
          none is connected. Nothing was changed.
        </p>
        <div className="actions">
          {access?.hosted && (
            <Link className="btn" to={`/projects/${scope.projectId}/assistant`}>
              Connect an AI key
            </Link>
          )}
          {onManual && <Button onClick={onManual}>Fill it in by hand</Button>}
        </div>
      </div>
    )
  }
  return <ErrorNotice error={error} prefix="The assistant could not do that" />
}

/**
 * Describe something in your own words; the assistant asks a few questions and
 * drafts the entries and the links between them. The draft goes to review, so
 * nothing is added to the story until the author has looked at it.
 */
export function AssistFlow({ entity, initialType, types, onClose, onManual }: Props) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()

  const [type, setType] = useState(initialType ?? '')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [sheet, setSheet] = useState<AssistQuestionSheet | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [showAllTypes, setShowAllTypes] = useState(false)

  const brief = (): AssistBrief =>
    entity
      ? { entity_id: entity.id, description: description.trim() }
      : { description: description.trim(), entity_type: type || null, name: name.trim() || null }

  const questions = useMutation({
    mutationFn: () => api.assistQuestions(scope, brief()),
    onSuccess: (data) => setSheet(data),
  })
  const draft = useMutation({
    mutationFn: async () => {
      const answered = (sheet?.questions ?? [])
        .filter((question) => (answers[question.id] ?? '').trim())
        .map((question) => ({
          question_id: question.id,
          question: question.question,
          answer: answers[question.id].trim(),
        }))
      // A draft is not a canon write: aim it at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.assistProposal(scope, { ...brief(), answers: answered, expected_revision: head })
    },
    onSuccess: async (result) => {
      await invalidate()
      onClose()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: { fromAssistant: true, discarded: result.discarded },
      })
    },
  })

  const ready = (): boolean => {
    if (!entity && !description.trim()) {
      setProblem('Write a sentence or two about it first.')
      return false
    }
    setProblem(null)
    return true
  }
  const manual = onManual ? () => onManual({ type: type || undefined, name: name.trim() }) : undefined
  const busy = questions.isPending || draft.isPending
  const anyAnswer = Object.values(answers).some((answer) => answer.trim())

  if (sheet) {
    const onSubmit = (event: FormEvent) => {
      event.preventDefault()
      draft.mutate()
    }
    return (
      <form className="stack" onSubmit={onSubmit} aria-label="A few questions">
        <p className="muted">
          Answer as many or as few as you like, in your own words. Mention anyone or anything
          connected — the assistant links what you already have and adds simple entries for
          anything new.
        </p>
        {sheet.questions.map((question, index) => (
          <div className="field" key={question.id}>
            <label htmlFor={`assist-${question.id}`}>{question.question}</label>
            <textarea
              id={`assist-${question.id}`}
              rows={2}
              data-autofocus={index === 0 ? true : undefined}
              value={answers[question.id] ?? ''}
              onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })}
            />
          </div>
        ))}
        <AssistantProblem error={draft.error} onManual={manual} />
        <div className="actions end">
          <Button variant="ghost" onClick={() => setSheet(null)} disabled={busy}>
            Back
          </Button>
          <Button
            variant="primary"
            type="submit"
            icon="spark"
            disabled={busy || (!anyAnswer && !description.trim())}
          >
            {draft.isPending ? 'Drafting…' : 'Draft it for me to review'}
          </Button>
        </div>
      </form>
    )
  }

  const usual = types.filter((item) => COMMON_TYPES.includes(item) || item === type)
  const choices = showAllTypes || usual.length === 0 ? types : usual

  return (
    <form
      className="stack"
      aria-label={entity ? `Fill in ${entity.name}` : 'Describe it'}
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (ready()) questions.mutate()
      }}
    >
      {!entity && (
        <fieldset>
          <legend>What is it?</legend>
          <div className="type-picker">
            <label className="type-option">
              <input type="radio" name="assist-type" checked={type === ''} onChange={() => setType('')} />
              <span className="avatar sm tint-violet" aria-hidden="true">
                <Icon name="spark" size={15} />
              </span>
              Let the assistant decide
            </label>
            {choices.map((item) => {
              const look = typeLook(item)
              return (
                <label key={item} className="type-option">
                  <input
                    type="radio"
                    name="assist-type"
                    checked={type === item}
                    onChange={() => setType(item)}
                  />
                  <span className={`avatar sm tint-${look.tint}`} aria-hidden="true">
                    <Icon name={look.icon} size={15} />
                  </span>
                  {typeLabel(item)}
                </label>
              )
            })}
          </div>
          {choices.length < types.length && (
            <div style={{ marginTop: '0.6rem' }}>
              <Button size="sm" variant="ghost" onClick={() => setShowAllTypes(true)}>
                Show all {types.length} types
              </Button>
            </div>
          )}
        </fieldset>
      )}

      {!entity && type && (
        <Field label={`${typeLabel(type)} name (optional)`}>
          {(props) => (
            <input {...props} value={name} autoComplete="off" onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
      )}

      <Field
        label={entity ? `What do you already know about ${entity.name}? (optional)` : 'Describe it in your own words'}
        hint={
          entity
            ? 'Skip this to go straight to the questions.'
            : 'A sentence or a page — one thing or several. Mention who and what it is connected to.'
        }
        error={problem}
      >
        {(props) => (
          <textarea
            {...props}
            data-autofocus
            rows={5}
            placeholder={entity ? undefined : EXAMPLE}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        )}
      </Field>

      <AssistantProblem error={questions.error ?? draft.error} onManual={manual} />
      <div className="actions end">
        {manual && (
          <Button variant="ghost" onClick={manual} disabled={busy}>
            Fill it in by hand
          </Button>
        )}
        <Button
          disabled={busy || !description.trim()}
          title={description.trim() ? undefined : 'Write a description first'}
          onClick={() => {
            if (ready()) draft.mutate()
          }}
        >
          {draft.isPending ? 'Drafting…' : 'Skip questions and draft it'}
        </Button>
        <Button variant="primary" type="submit" icon="chat" disabled={busy}>
          {questions.isPending ? 'Thinking of questions…' : 'Ask me a few questions'}
        </Button>
      </div>
    </form>
  )
}
