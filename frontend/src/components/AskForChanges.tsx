import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { plural } from '../lib/words'
import { useProject } from '../project'
import { AssistantProblem } from './AssistFlow'
import { Button, Field } from './ui'

/**
 * Say what should be different about a set of suggested changes. The assistant
 * edits or skips the changes already there; the set stays waiting for review.
 */
export function AskForChanges({
  mutationId,
  onRevised,
}: {
  mutationId: string
  onRevised: () => Promise<unknown>
}) {
  const { scope } = useProject()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const ask = useMutation({
    mutationFn: () => api.assistRevision(scope, { mutation_id: mutationId, instruction: text.trim() }),
    onSuccess: async (result) => {
      // What was asked stays in the box until it has had an effect.
      if (result.edited + result.skipped > 0) setText('')
      await onRevised()
    },
  })
  const result = ask.data

  if (!open) {
    return (
      <div>
        <Button size="sm" icon="spark" onClick={() => setOpen(true)}>
          Ask the assistant to change these
        </Button>
      </div>
    )
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (text.trim()) ask.mutate()
  }
  const did = result
    ? [
        result.edited > 0 ? `edited ${plural(result.edited, 'change')}` : '',
        result.skipped > 0 ? `skipped ${plural(result.skipped, 'change')}` : '',
      ].filter(Boolean)
    : []

  return (
    <form className="stack change-editor" onSubmit={onSubmit} aria-label="Ask the assistant to change these">
      <Field
        label="What should be different?"
        hint="For example: “the professor is called Hale and is sixty”, or “leave out the fact about the lighthouse”. The assistant edits or skips the changes below. It cannot add new ones here."
      >
        {(props) => (
          <textarea
            {...props}
            data-autofocus
            rows={3}
            value={text}
            disabled={ask.isPending}
            onChange={(event) => setText(event.target.value)}
          />
        )}
      </Field>
      <AssistantProblem error={ask.error} />
      {result && !ask.isPending && (
        <div className="notice plain" role="status">
          {did.length > 0 && (
            <p>
              <strong>Done.</strong> The assistant {did.join(' and ')}. Nothing has been added to
              your story; look the changes over below.
            </p>
          )}
          {result.questions.length > 0 && (
            <>
              <p>
                <strong>The assistant needs to know more.</strong> Add the answer to what you
                wrote and ask again.
              </p>
              <ul>
                {result.questions.map((question, index) => (
                  <li key={index}>{question}</li>
                ))}
              </ul>
            </>
          )}
          {result.notes.length > 0 && (
            <>
              <p>
                <strong>It could not do this here,</strong> because it needs a new change. Use
                “Change anything” for it.
              </p>
              <ul>
                {result.notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </>
          )}
          {result.discarded.length > 0 && (
            <>
              <p>Left as they were:</p>
              <ul>
                {result.discarded.map((item, index) => (
                  <li key={index}>{item.reason}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      <div className="actions">
        <Button variant="primary" type="submit" icon="spark" disabled={ask.isPending || !text.trim()}>
          {ask.isPending ? 'Working…' : 'Ask'}
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
    </form>
  )
}
