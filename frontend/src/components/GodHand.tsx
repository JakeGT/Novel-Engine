import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type CanonEntity } from '../api/client'
import { useInvalidateProject, useProject } from '../project'
import { AssistantProblem } from './AssistFlow'
import { Button, Dialog, Field } from './ui'

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
export function GodHand({ entity, onClose }: { entity?: CanonEntity; onClose: () => void }) {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const [instruction, setInstruction] = useState('')
  const [clarification, setClarification] = useState('')
  const [questions, setQuestions] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)

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
        entity_id: entity?.id ?? null,
        expected_revision: head,
      })
    },
    onSuccess: async (result) => {
      if (!result.mutation_id) {
        // Ambiguous: nothing was proposed. Show what the assistant needs to know.
        setQuestions(result.questions)
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

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!instruction.trim()) return setProblem('Say what should change.')
    setProblem(null)
    change.mutate()
  }

  return (
    <Dialog title="The God Hand" onClose={onClose} wide>
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
