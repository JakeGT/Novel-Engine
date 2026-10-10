import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useProject } from '../project'
import { Button, ErrorNotice, Field } from './ui'

const number = (value: number) => value.toLocaleString('en-GB')
const period = (hours: number) => (hours === 24 ? 'the last day' : `the last ${hours} hours`)

function useAllowance(workspaceId: string | null | undefined) {
  return useQuery({
    queryKey: ['ai-usage', workspaceId],
    queryFn: () => api.aiUsage(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
}

/** Said before more is spent, and only once most of the allowance has gone. */
export function AllowanceNote() {
  const { scope } = useProject()
  const usage = useAllowance(scope.workspaceId)
  const found = usage.data
  if (!found || !found.limit_tokens) return null
  const share = found.used_tokens / found.limit_tokens
  if (share < 0.8) return null
  return (
    <p className="notice plain" role="note">
      {share >= 1
        ? 'Your assistant allowance is used up for now. It frees up again over the next day.'
        : `You have used ${Math.floor(share * 100)}% of your assistant allowance for ${period(found.period_hours)}.`}
    </p>
  )
}

/** How much of the allowance is spent, and (for an owner) what the allowance is. */
export function AllowanceCard({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const usage = useAllowance(workspaceId)
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (tokens: number | null) => api.setAiUsage(workspaceId, tokens),
    onSuccess: async () => {
      setText(null)
      await queryClient.invalidateQueries({ queryKey: ['ai-usage', workspaceId] })
    },
  })
  const found = usage.data
  if (!found) return <ErrorNotice error={usage.error} prefix="Could not load the allowance" />

  const value = text ?? (found.workspace_limit_tokens ? String(found.workspace_limit_tokens) : '')
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = value.replace(/[,\s]/g, '')
    if (trimmed === '') {
      setProblem(null)
      return save.mutate(null)
    }
    if (!/^\d+$/.test(trimmed)) return setProblem('Enter a whole number, or leave it empty.')
    setProblem(null)
    save.mutate(Number(trimmed))
  }

  return (
    <section className="card" aria-labelledby="allowance-heading">
      <div className="card-head">
        <div>
          <h2 id="allowance-heading">Daily allowance</h2>
          <p className="muted">
            Everyone you invite uses your key. This is the most one person can spend through it in
            a day, counted in tokens: a token is roughly three-quarters of a word, and a draft
            spends them on what it reads as well as what it writes.
          </p>
        </div>
      </div>
      <p>
        You have used <strong>{number(found.used_tokens)}</strong>
        {found.limit_tokens ? (
          <>
            {' '}
            of <strong>{number(found.limit_tokens)}</strong>
          </>
        ) : null}{' '}
        tokens in {period(found.period_hours)}, over {number(found.requests)}{' '}
        {found.requests === 1 ? 'request' : 'requests'}.
      </p>
      {found.limit_tokens ? (
        <progress
          className="meter"
          aria-label="Share of your allowance used"
          max={found.limit_tokens}
          value={Math.min(found.used_tokens, found.limit_tokens)}
        />
      ) : null}
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field
          label="Allowance for each person, per day (tokens)"
          hint={
            found.site_limit_tokens
              ? `Leave it empty to use this site’s limit of ${number(found.site_limit_tokens)}. A lower number here takes its place.`
              : 'Leave it empty for no limit.'
          }
          error={problem}
        >
          {(props) => (
            <input
              {...props}
              inputMode="numeric"
              autoComplete="off"
              placeholder="200,000"
              value={value}
              onChange={(event) => setText(event.target.value)}
            />
          )}
        </Field>
        <ErrorNotice error={save.error} prefix="The allowance was not changed" />
        <div className="actions">
          <Button variant="primary" type="submit" disabled={save.isPending || text === null}>
            {save.isPending ? 'Saving…' : 'Save allowance'}
          </Button>
          {save.isSuccess && text === null && (
            <span className="hint" role="status">
              Saved.
            </span>
          )}
        </div>
      </form>
    </section>
  )
}
