import { AllowanceNote } from './Allowance'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type AssistSession, type CanonEntity, type ChangeProposal } from '../api/client'
import { useWorkingWhatIf } from '../lib/whatif'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import { AssistantProblem } from './AssistFlow'
import { SetupSuggestions } from './Conversations'
import { RestoreDialog } from './RestoreDialog'
import { Button, Dialog, Field } from './ui'

// Where the things an author may ask for, that are not story changes, are done.
const PLACES: Record<string, { path: string; label: string }> = {
  history: { path: 'history', label: 'Open History' },
  people: { path: 'people', label: 'Open People' },
  setup: { path: 'customize', label: 'Open Types and fields' },
  assistant: { path: 'assistant', label: 'Open the assistant’s settings' },
  media: { path: 'media', label: 'Open Media' },
}

const EXAMPLES = [
  'Mara is Tobin’s sister, not his rival.',
  'Move the Harbor Guild to Saltmere.',
  'Rename Lantern Harbor to Lanternhaven.',
  'These two are the same person — merge them.',
]

/**
 * The God Hand: say what should change, in any words, and the assistant works out
 * every change that implies. It can reach everything and decide nothing: the
 * result is a set of suggested changes to review, or a question when the
 * instruction could mean more than one thing.
 */
export function GodHand({
  entity,
  resume,
  onClose,
}: {
  entity?: CanonEntity
  /** An unfinished conversation: the instruction and what the assistant asked about it. */
  resume?: AssistSession
  onClose: () => void
}) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [instruction, setInstruction] = useState(resume?.description ?? '')
  const [clarification, setClarification] = useState('')
  const [questions, setQuestions] = useState<string[]>(
    (resume?.questions ?? []).map((item) => item.question),
  )
  // Once the assistant has asked something, the conversation is kept under this id.
  const [sessionId, setSessionId] = useState<string | null>(resume?.id ?? null)
  const focusId = entity?.id ?? resume?.entity_id ?? null
  const [problem, setProblem] = useState<string | null>(null)
  const [elsewhere, setElsewhere] = useState<NonNullable<ChangeProposal['elsewhere']>>([])
  const [backTo, setBackTo] = useState<number | null>(null)
  const [invite, setInvite] = useState<ChangeProposal['invite']>(null)
  const [restoring, setRestoring] = useState(false)
  // Setup suggestions that came with no change are kept with the conversation.
  const [suggestedIn, setSuggestedIn] = useState<string | null>(null)
  const suggestions = useQuery({
    queryKey: scopedKey(scope, 'assist-session', suggestedIn),
    queryFn: () => api.assistSession(scope, suggestedIn as string),
    enabled: Boolean(suggestedIn),
  })

  const change = useMutation({
    mutationFn: async () => {
      // Answers to the assistant's questions travel with the original instruction.
      const full = clarification.trim()
        ? `${instruction.trim()}\n\nTo be clear: ${clarification.trim()}`
        : instruction.trim()
      // A proposal is not a canon write: aim it at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.assistChange(scope, {
        instruction: full,
        entity_id: focusId,
        session_id: sessionId,
        expected_revision: head,
      })
    },
    onSuccess: async (result) => {
      void queryClient.invalidateQueries({ queryKey: scopedKey(scope, 'assist-sessions') })
      if (!result.mutation_id) {
        // Ambiguous: nothing was proposed. Show what the assistant needs to know.
        setQuestions(result.questions)
        setElsewhere(result.elsewhere ?? [])
        setBackTo(result.back_to_version ?? null)
        setInvite(result.invite ?? null)
        setSuggestedIn((result.suggestions ?? []).length > 0 ? result.session_id : null)
        // A conversation that only pointed elsewhere is not kept.
        setSessionId(result.questions.length > 0 || (result.suggestions ?? []).length > 0 ? result.session_id : null)
        return
      }
      await invalidate()
      onClose()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: {
          fromAssistant: true,
          understood: result.understood,
          notes: result.notes,
          discarded: result.discarded,
        },
      })
    },
  })

  // Offered by need (Level 5): "What if…" is a question about another turn of the
  // story, so it can be tried in a what-if, leaving the story itself untouched.
  const { whatIf: working, set: work } = useWorkingWhatIf(scope.projectId)
  const soundsLikeWhatIf = /^\s*what\s+if\b/i.test(instruction) && !working
  const tryWhatIf = useMutation({
    mutationFn: async () => {
      const name = instruction.trim().replace(/\s+/g, ' ').slice(0, 120)
      const made = await api.startWhatIf(scope, { name })
      const inside = { ...scope, branchId: made.id }
      const result = await api.assistChange(inside, {
        instruction: instruction.trim(),
        entity_id: focusId,
        expected_revision: made.version,
      })
      return { made, result }
    },
    onSuccess: async ({ made, result }) => {
      work({ id: made.id, name: made.name })
      await invalidate()
      onClose()
      navigate(
        result.mutation_id
          ? `/projects/${scope.projectId}/review/${result.mutation_id}`
          : `/projects/${scope.projectId}/what-ifs/${made.id}`,
        { state: { fromAssistant: true, understood: result.understood, notes: result.notes, discarded: result.discarded } },
      )
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!instruction.trim()) return setProblem('Say what should change.')
    setProblem(null)
    change.mutate()
  }

  // One dialog at a time: the preview of going back takes this one's place, and
  // closing it without going back returns here with everything as it was.
  if (restoring && backTo !== null) {
    return (
      <RestoreDialog
        revision={backTo}
        onClose={() => setRestoring(false)}
        onDone={() => {
          void invalidate()
          onClose()
        }}
      />
    )
  }

  return (
    <Dialog title="Change anything" onClose={onClose} wide>
      <form className="stack" onSubmit={onSubmit} noValidate aria-label="Change anything">
        <p className="muted">
          Say what should change{entity ? ` about ${entity.name}, or anything else` : ''}, in your
          own words. The assistant works out everything that follows from it and shows you each
          change before anything is saved.
        </p>
        <Field label="What should change?" error={problem}>
          {(props) => (
            <textarea
              {...props}
              data-autofocus
              rows={4}
              value={instruction}
              onChange={(event) => {
                setInstruction(event.target.value)
                setQuestions([])
                setElsewhere([])
                setBackTo(null)
                setInvite(null)
                setSuggestedIn(null)
              }}
            />
          )}
        </Field>

        {!instruction && (
          <div className="chips" role="group" aria-label="Examples">
            {EXAMPLES.map((example) => (
              <button key={example} type="button" className="chip" onClick={() => setInstruction(example)}>
                {example}
              </button>
            ))}
          </div>
        )}

        {soundsLikeWhatIf && (
          <div className="notice plain" role="note">
            <p>
              <strong>This sounds like a what-if.</strong> Try it in a what-if instead, and your
              story stays exactly as it is while you see what would change.
            </p>
            <Button icon="branch" disabled={tryWhatIf.isPending} onClick={() => tryWhatIf.mutate()}>
              {tryWhatIf.isPending ? 'Starting the what-if…' : 'Try it in a what-if'}
            </Button>
            <AssistantProblem error={tryWhatIf.error} />
          </div>
        )}

        {questions.length > 0 && (
          <div className="notice plain" role="status">
            <p>
              <strong>Before changing anything, the assistant needs to know:</strong>
            </p>
            <ul>
              {questions.map((question, index) => (
                <li key={index}>• {question}</li>
              ))}
            </ul>
            <p className="hint">
              This is kept. You can close it and pick it up later from the project’s home page.
            </p>
            <Field label="Your answer">
              {(props) => (
                <textarea
                  {...props}
                  rows={2}
                  value={clarification}
                  onChange={(event) => setClarification(event.target.value)}
                />
              )}
            </Field>
          </div>
        )}

        {elsewhere.length > 0 && (
          <div className="notice plain" role="status">
            <p>
              <strong>That is not a change to your story bible,</strong> so nothing was changed.
              It is done here:
            </p>
            <ul className="rows">
              {elsewhere.map((item) => {
                const place = PLACES[item.place]
                const to = `/projects/${scope.projectId}/${place?.path ?? ''}`
                return (
                  <li className="row" key={item.place}>
                    <span className="grow">{item.what}</span>
                    {item.place === 'history' && backTo !== null ? (
                      // The author sees exactly what going back would change before it happens.
                      <Button size="sm" icon="undo" onClick={() => setRestoring(true)}>
                        See what going back to version {backTo} would change
                      </Button>
                    ) : item.place === 'people' && invite ? (
                      <Link className="btn sm" to={to} state={{ invite }} onClick={onClose}>
                        Open People with this filled in
                      </Link>
                    ) : (
                      place && (
                        <Link className="btn sm" to={to} onClick={onClose}>
                          {place.label}
                        </Link>
                      )
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        )}

        {/* What the story bible cannot record yet: an owner adds it here and asks again. */}
        {suggestions.data && suggestions.data.suggestions.length > 0 && (
          <SetupSuggestions session={suggestions.data} onLeave={onClose} />
        )}

        <AllowanceNote reads="story" sessionId={sessionId} />

        <AssistantProblem error={change.error} />
        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" icon="hand" disabled={change.isPending}>
            {change.isPending ? 'Working it out…' : 'Show me what would change'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
