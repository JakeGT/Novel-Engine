import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type NameQuestion, type SourceTextDetail } from '../api/client'
import { typeLabel } from '../lib/words'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { Button, ErrorNotice, WriteProblem } from './ui'

/**
 * "Who is this?", one phrase at a time. An answer is remembered, so the same
 * question is not asked again in the next text.
 */
export function NameQuestions({
  detail,
  entries,
}: {
  detail: SourceTextDetail
  entries: CanonEntity[]
}) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const [later, setLater] = useState<string[]>([])
  const [onlyHere, setOnlyHere] = useState(false)
  const [other, setOther] = useState('')
  const [newType, setNewType] = useState('Character')
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const types = (vocabulary.data?.entity_types ?? []).map((item) => item.name)

  const waiting = detail.questions.filter((question) => !later.includes(question.phrase))
  const question: NameQuestion | undefined = waiting[0]
  const refresh = async () => {
    setOther('')
    await client.invalidateQueries({ queryKey: scopedKey(scope, 'text') })
    await client.invalidateQueries({ queryKey: scopedKey(scope, 'names') })
  }

  const decide = useMutation({
    mutationFn: (answer: { verdict: 'is' | 'is_not' | 'ignore'; entityId?: string }) =>
      api.decideName(scope, {
        phrase: question?.phrase ?? '',
        verdict: answer.verdict,
        entity_id: answer.entityId ?? null,
        source_id: onlyHere && answer.verdict !== 'ignore' ? detail.text.id : null,
      }),
    onSuccess: refresh,
  })
  const addEntry = useCanonWrite(
    (expectedRevision, phrase: string) =>
      api.addEntryFromText(scope, detail.text.id, {
        phrase,
        entity_type: newType,
        expected_revision: expectedRevision,
      }),
    () => void refresh(),
  )

  if (!question) {
    if (detail.questions.length === 0) return null
    return (
      <section className="card stack tight" aria-label="Who is this?">
        <p className="muted">
          {detail.questions.length === 1 ? 'One name is' : `${detail.questions.length} names are`} still
          unanswered.
        </p>
        <div className="actions">
          <Button size="sm" onClick={() => setLater([])}>
            Ask me now
          </Button>
        </div>
      </section>
    )
  }

  const busy = decide.isPending || addEntry.pending
  const nameOf = (entityId: string) => detail.names[entityId] ?? 'an entry'
  const around = (from: number, to: number) => detail.content.slice(Math.max(0, from), to).replace(/\s+/g, ' ')
  const choices = entries.filter(
    (entry) => entry.lifecycle === 'active' && !question.candidates.includes(entry.id),
  )

  return (
    <section className="card stack tight name-question" aria-labelledby="who-heading">
      <div className="actions" style={{ justifyContent: 'space-between' }}>
        <h2 id="who-heading">Who is this?</h2>
        {detail.questions.length > 1 && (
          <span className="muted small">{detail.questions.length} to answer</span>
        )}
      </div>
      <p>
        <strong>“{question.phrase}”</strong>{' '}
        <span className="muted">
          {question.count === 1 ? 'appears once' : `appears ${question.count} times`}
        </span>
      </p>
      <blockquote className="small">
        …{around(question.first_start - 70, question.first_start)}
        <mark>{detail.content.slice(question.first_start, question.first_end)}</mark>
        {around(question.first_end, question.first_end + 70)}…
      </blockquote>
      {question.guessed && (
        <p className="muted small">The assistant thinks this means {nameOf(question.candidates[0])}.</p>
      )}

      {canWrite ? (
        <>
          <div className="actions">
            {question.candidates.map((entityId) => (
              <Button
                key={entityId}
                size="sm"
                variant="primary"
                disabled={busy}
                onClick={() => decide.mutate({ verdict: 'is', entityId })}
              >
                It is {nameOf(entityId)}
              </Button>
            ))}
            {question.guessed && (
              <Button
                size="sm"
                disabled={busy}
                onClick={() => decide.mutate({ verdict: 'is_not', entityId: question.candidates[0] })}
              >
                Not {nameOf(question.candidates[0])}
              </Button>
            )}
          </div>

          <div className="actions">
            <label className="sr-only" htmlFor="who-other">
              Someone or something else
            </label>
            <select id="who-other" value={other} onChange={(event) => setOther(event.target.value)}>
              <option value="">Someone or something else…</option>
              {choices.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name} ({typeLabel(entry.type)})
                </option>
              ))}
            </select>
            <Button
              size="sm"
              disabled={busy || !other}
              onClick={() => decide.mutate({ verdict: 'is', entityId: other })}
            >
              That one
            </Button>
          </div>

          <div className="actions">
            <label className="sr-only" htmlFor="who-type">
              Type of the new entry
            </label>
            <select id="who-type" value={newType} onChange={(event) => setNewType(event.target.value)}>
              {(types.length > 0 ? types : ['Character']).map((type) => (
                <option key={type} value={type}>
                  {typeLabel(type)}
                </option>
              ))}
            </select>
            <Button size="sm" icon="plus" disabled={busy} onClick={() => void addEntry.submit(question.phrase)}>
              Add as a new entry
            </Button>
          </div>

          <div className="actions">
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => decide.mutate({ verdict: 'ignore' })}>
              Not a name
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setLater([...later, question.phrase])}>
              Ask me later
            </Button>
            <label className="check small">
              <input type="checkbox" checked={onlyHere} onChange={(event) => setOnlyHere(event.target.checked)} />
              Only in this text
            </label>
          </div>
          <ErrorNotice error={decide.error} prefix="Could not remember that" />
          <WriteProblem write={addEntry} scope={scope} />
        </>
      ) : (
        <p className="muted small">You can read this story but not change it.</p>
      )}
    </section>
  )
}
