import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'
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

/**
 * Said before anything is spent: about how much this request will read and reply, what
 * is left of the allowance and of the conversation's budget, and whether it will be
 * confirmed first. A warning once most of the allowance has gone.
 */
export function AllowanceNote({ reads, sessionId }: { reads?: 'story' | 'entry'; sessionId?: string | null }) {
  const { scope } = useProject()
  const usage = useAllowance(scope.workspaceId)
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'assist-estimate', sessionId ?? null),
    queryFn: () => api.assistEstimate(scope, sessionId),
    enabled: Boolean(reads),
    staleTime: 60_000,
  })
  const found = usage.data
  const share = found?.limit_tokens ? found.used_tokens / found.limit_tokens : 0
  const tokens = reads && estimate.data ? estimate.data[`${reads}_tokens`] : null
  const left = found?.limit_tokens ? Math.max(found.limit_tokens - found.used_tokens, 0) : null
  const limits = reads ? estimate.data : undefined
  const reply = limits?.reply_tokens ?? 0
  const asksFirst =
    tokens !== null && limits?.confirm_above_tokens ? tokens + reply > limits.confirm_above_tokens : false
  const budget = sessionId && limits?.conversation_tokens ? limits.conversation_tokens : null
  const extra = (
    <>
      {asksFirst &&
        ` That is more than the ${number(limits?.confirm_above_tokens ?? 0)} you asked to be asked about, so you will be asked before it is sent.`}
      {budget !== null &&
        ` This conversation has used ${number(limits?.conversation_used_tokens ?? 0)} of the ${number(budget)} tokens it may use.`}
    </>
  )

  if (share >= 1) {
    return (
      <p className="notice plain" role="note">
        Your assistant allowance is used up for now. It frees up again over the next day.
      </p>
    )
  }
  if (tokens === null && share < 0.8) return null
  return (
    <p className={share >= 0.8 ? 'notice plain' : 'hint'} role="note">
      {tokens !== null &&
        `This will read about ${number(tokens)} tokens${reads === 'story' ? ' (your whole story bible)' : ''}, ${reply ? `plus up to ${number(reply)} for the reply` : 'plus the reply'}. `}
      {found && left !== null
        ? share >= 0.8
          ? `You have used ${Math.floor(share * 100)}% of your allowance for ${period(found.period_hours)}: ${number(left)} tokens are left.`
          : `${number(left)} tokens of your allowance are left for ${period(found.period_hours)}.`
        : ''}
      {extra}
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
        {found.cost_usd ? ` Your provider reported that this cost $${found.cost_usd.toFixed(2)}.` : ''}
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
