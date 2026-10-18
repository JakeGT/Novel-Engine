import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type KeepRule, type KeptSettings } from '../api/client'
import { useKeptSettings } from '../lib/kept'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, ErrorNotice, Loading } from './ui'

function onKinds(settings: KeptSettings): string[] {
  return settings.rules.filter((rule) => rule.on).map((rule) => rule.kind)
}

function approvals(rule: KeepRule, needed: number): string {
  if (rule.hand_approvals >= needed) {
    return `You have approved ${plural(rule.hand_approvals, 'suggestion')} like this by hand.`
  }
  return `You have approved ${rule.hand_approvals} of ${needed} by hand. Approve ${needed - rule.hand_approvals} more to switch this on.`
}

/**
 * The author's rules, read like a mail filter: when the assistant suggests one of these,
 * keep it without asking. Each saves as soon as it is ticked, and switching off is never
 * refused.
 */
export function KeptRules() {
  const { canWrite } = useProject()
  const { settings, save } = useKeptSettings()
  const [said, setSaid] = useState<string | null>(null)
  if (settings.isPending) return <Loading>Opening your rules…</Loading>
  if (!settings.data) return <ErrorNotice error={settings.error} prefix="Could not open your rules" />
  const data = settings.data
  const kinds = onKinds(data)
  const needed = data.hand_approvals_needed
  const apply = (next: string[], allow: boolean, message: string) => {
    setSaid(null)
    save.mutate({ kinds: next, allow_clients: allow }, { onSuccess: () => setSaid(message) })
  }
  const toggle = (rule: KeepRule, on: boolean) =>
    apply(
      on ? [...kinds, rule.kind] : kinds.filter((kind) => kind !== rule.kind),
      data.allow_clients,
      on ? `“${rule.name}” will be kept for you from now on.` : `“${rule.name}” will wait for you again.`,
    )

  return (
    <div className="stack">
      {data.workspace_forbidden && (
        <p className="notice warn" role="note">
          A workspace owner has turned this off for every story here, so everything waits for you.
          Your choices below are kept for when it is turned back on.
        </p>
      )}
      <fieldset className="stack" aria-describedby="kept-rules-hint">
        <legend className="kept-legend">When the assistant suggests…</legend>
        <p id="kept-rules-hint" className="hint">
          …keep it in your story without asking. Each is listed here with an Undo. Only kinds you
          have approved by hand {needed} times can be switched on.
        </p>
        <ul className="kept-rules">
          {data.rules.map((rule) => {
            const earned = rule.hand_approvals >= needed
            const locked = !canWrite || save.isPending || (!rule.on && (!earned || data.workspace_forbidden))
            return (
              <li key={rule.kind}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={rule.on}
                    disabled={locked}
                    aria-describedby={`kept-rule-${rule.kind}`}
                    onChange={(event) => toggle(rule, event.target.checked)}
                  />
                  <span className="kept-rule-text">
                    <span className="kept-rule-name">{rule.name}</span>
                    <span id={`kept-rule-${rule.kind}`} className="muted small">
                      {rule.description} {approvals(rule, needed)}
                    </span>
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
      </fieldset>
      <ErrorNotice error={save.error} prefix="That was not changed" />
      {said && (
        <p className="notice ok" role="status">
          {said}
        </p>
      )}
      {kinds.length > 0 && canWrite && (
        <div className="actions">
          <Button
            disabled={save.isPending}
            onClick={() => apply([], data.allow_clients, 'Switched off. Every suggestion waits for you again.')}
          >
            Switch it all off
          </Button>
        </div>
      )}
      <div>
        <h3 className="kept-subhead">Always waits for you</h3>
        <ul className="kept-always muted small">
          {data.always_waits.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/**
 * On the review queue: what was kept since the author last looked, or, once they have
 * approved enough of one kind by hand, the offer to have suggestions like it kept.
 */
export function KeptSinceNotice() {
  const { scope, canWrite } = useProject()
  const { settings } = useKeptSettings()
  const data = settings.data
  if (!data) return null
  const to = `/projects/${scope.projectId}/review/kept`
  if (data.kept_since_seen > 0) {
    return (
      <p className="notice ok kept-note" role="status">
        <span>
          {plural(data.kept_since_seen, 'suggestion was', 'suggestions were')} kept for you since you
          last looked.
        </span>{' '}
        <Link to={to}>See what was kept</Link>
      </p>
    )
  }
  const anyOn = data.rules.some((rule) => rule.on)
  const ready = data.rules.find((rule) => !rule.on && rule.hand_approvals >= data.hand_approvals_needed)
  if (!anyOn && ready && canWrite && !data.workspace_forbidden) {
    return (
      <p className="notice plain kept-note" role="note">
        <span>
          You have approved {ready.hand_approvals} {ready.name.toLowerCase()} from the assistant by
          hand. Suggestions like these can be kept for you without asking, each listed with an Undo.
        </span>{' '}
        <Link to={to}>Choose what to keep</Link>
      </p>
    )
  }
  return null
}

/** On one suggestion: whether it was kept for the author, or why it waited for them. */
export function KeepDecisionNote({ mutationId }: { mutationId: string }) {
  const { scope } = useProject()
  const decision = useQuery({
    queryKey: scopedKey(scope, 'keep-decision', mutationId),
    queryFn: () => api.keepDecision(scope, mutationId),
  })
  const data = decision.data
  if (!data?.how || data.how === 'by_hand') return null
  const to = `/projects/${scope.projectId}/review/kept`
  if (data.how === 'kept') {
    return (
      <p className="notice ok kept-note" role="note">
        {data.note} <Link to={to}>See everything kept for you</Link>
      </p>
    )
  }
  return (
    <p className="notice plain" role="note">
      This was not kept for you: {data.reason} <Link to={to}>Your rules</Link>
    </p>
  )
}

/** A workspace owner can turn keeping suggestions off for every story. */
export function WorkspaceKeptCard({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const state = useQuery({
    queryKey: ['ai-kept', workspaceId],
    queryFn: () => api.workspaceKept(workspaceId),
  })
  const save = useMutation({
    mutationFn: (forbidden: boolean) => api.setWorkspaceKept(workspaceId, forbidden),
    onSuccess: async (saved) => {
      client.setQueryData(['ai-kept', workspaceId], saved)
      await client.invalidateQueries({ queryKey: ['kept-settings'] })
    },
  })
  if (!state.data) return <ErrorNotice error={state.error} prefix="Could not load this setting" />
  const forbidden = state.data.forbidden
  return (
    <section className="card" aria-labelledby="ws-kept-heading">
      <div className="card-head">
        <div>
          <h2 id="ws-kept-heading">Keeping suggestions without asking</h2>
          <p className="muted">
            Authors can choose to have some of the assistant’s suggestions added to their story
            without asking, after approving them by hand several times. You can turn that off for
            every story in this workspace.
          </p>
        </div>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={forbidden}
          disabled={save.isPending}
          onChange={(event) => save.mutate(event.target.checked)}
        />
        <span>Turn it off for every story here</span>
      </label>
      <ErrorNotice error={save.error} prefix="That was not changed" />
      {save.isSuccess && (
        <p className="notice ok" role="status">
          {forbidden
            ? 'Turned off. Every suggestion now waits for its author.'
            : 'Turned back on. Each story uses the rules its authors chose.'}
        </p>
      )}
    </section>
  )
}
