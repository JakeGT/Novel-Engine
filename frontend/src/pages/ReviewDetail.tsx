import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import {
  api,
  errorMessage,
  isStaleHead,
  type MutationDetail as Detail,
  type MutationOperation,
  type MutationValidation,
} from '../api/client'
import { Icon } from '../components/icons'
import {
  Badge,
  Button,
  ChangeStateBadge,
  ErrorNotice,
  Field,
  Json,
  Loading,
  ReadOnlyNotice,
} from '../components/ui'
import { fullDate, timeAgo } from '../lib/time'
import {
  CHANGE_STATES,
  REVIEW_REASONS,
  changeSetTitle,
  describeOperation,
  namesFromOperations,
  originLabel,
  plural,
  valueText,
  words,
} from '../lib/words'
import { actorLabel, scopedKey, useEntries, useInvalidateProject, useProject } from '../project'

const OPEN = new Set(['draft', 'validating', 'needs_review', 'approved'])
const HIDDEN_KEYS = new Set(['op', 'id', 'evidence', 'proposition', 'project_id'])

type Issue = MutationValidation['issues'][number]

/** Changes of one kind are reviewed together, in the order they will be applied. */
const GROUPS: { label: string; types: string[] }[] = [
  { label: 'New entries', types: ['create_entity'] },
  { label: 'Changes to entries', types: ['update_entity'] },
  { label: 'Facts ended', types: ['close_fact'] },
  { label: 'New facts', types: ['assert_fact'] },
  { label: 'Beliefs ended', types: ['close_belief'] },
  { label: 'New beliefs', types: ['assert_belief'] },
  { label: 'Events', types: ['create_event', 'amend_event', 'retire_event'] },
  { label: 'Archived', types: ['retire_entity'] },
]

function groupOperations(operations: MutationOperation[]): { label: string; items: MutationOperation[] }[] {
  const known = new Set(GROUPS.flatMap((group) => group.types))
  const groups = GROUPS.map((group) => ({
    label: group.label,
    items: operations.filter((operation) => group.types.includes(operation.operation_type)),
  }))
  groups.push({
    label: 'Other changes',
    items: operations.filter((operation) => !known.has(operation.operation_type)),
  })
  return groups.filter((group) => group.items.length > 0)
}

/** Field-by-field rows for a change: what it sets, and what was there before when it edits. */
function fieldRows(operation: MutationOperation): { key: string; before?: unknown; after: unknown }[] {
  const before = operation.target_before ?? null
  return Object.entries(operation.payload)
    .filter(([key, value]) => !HIDDEN_KEYS.has(key) && value !== null && value !== undefined)
    .filter(([, value]) => !(Array.isArray(value) && value.length === 0))
    .filter(([, value]) => !(typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0))
    .filter(([key, value]) => !before || !(key in before) || JSON.stringify(before[key]) !== JSON.stringify(value))
    .map(([key, value]) => ({ key, before: before && key in before ? before[key] : undefined, after: value }))
}

function ChangeCard({
  operation,
  title,
  issues,
  editable,
  busy,
  onDecide,
  onRevise,
}: {
  operation: MutationOperation
  title: string
  issues: Issue[]
  editable: boolean
  busy: boolean
  onDecide: (decision: 'accepted' | 'rejected', note: string | null) => void
  onRevise: (payload: Record<string, unknown>, reason: string | null) => Promise<unknown>
}) {
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const number = operation.ordinal + 1
  const decision = operation.decision?.decision
  const skipped = decision === 'rejected'
  const rows = fieldRows(operation)
  const isEdit = Boolean(operation.target_before && Object.keys(operation.target_before).length > 0)
  const gone = operation.target_before !== null && operation.target_before !== undefined && !isEdit

  const save = async () => {
    let payload: unknown
    try {
      payload = JSON.parse(text)
    } catch (failure) {
      return setProblem(`Not valid JSON: ${errorMessage(failure)}`)
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return setProblem('The change must be a JSON object.')
    }
    try {
      await onRevise(payload as Record<string, unknown>, reason.trim() || null)
      setProblem(null)
      setEditing(false)
    } catch (failure) {
      // Keep the editor open with what was typed.
      setProblem(errorMessage(failure))
    }
  }

  return (
    <li className={`change ${skipped ? 'skipped' : ''}`}>
      <div className="change-head">
        <span className="change-title">{title}</span>
        {operation.revision > 1 && <Badge tone="info">Edited</Badge>}
        {editable ? (
          <div className="segmented" role="group" aria-label={`Change ${number}: ${title}`}>
            <button
              type="button"
              aria-pressed={decision === 'accepted'}
              disabled={busy}
              onClick={() => onDecide('accepted', note.trim() || null)}
            >
              Keep
            </button>
            <button
              type="button"
              aria-pressed={skipped}
              disabled={busy}
              onClick={() => onDecide('rejected', note.trim() || null)}
            >
              Skip
            </button>
          </div>
        ) : (
          <Badge tone={skipped ? 'danger' : decision === 'accepted' ? 'ok' : 'neutral'}>
            {skipped ? 'Skipped' : decision === 'accepted' ? 'Kept' : 'Included'}
          </Badge>
        )}
      </div>
      {skipped && <p className="muted small">Skipped — this will not be added.</p>}
      {operation.decision?.note && <p className="muted small">Note: “{operation.decision.note}”</p>}
      {gone && (
        <p className="notice warn" role="note">
          The thing this change was about no longer exists in your story.
        </p>
      )}

      {issues.map((issue, index) => (
        <p
          key={index}
          className={`notice ${issue.severity === 'blocking' ? 'danger' : 'plain'}`}
          role={issue.severity === 'blocking' ? 'alert' : 'note'}
        >
          <span>
            <strong>{issue.severity === 'blocking' ? 'This must be fixed first' : 'Worth a look'}:</strong>{' '}
            {issue.message}
          </span>
        </p>
      ))}

      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="diff-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Field</span>
                </th>
                {isEdit && <th scope="col">Now</th>}
                <th scope="col">{isEdit ? 'Would become' : 'Value'}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{words(row.key)}</th>
                  {isEdit && <td className="was">{valueText(row.before)}</td>}
                  <td className="now">{valueText(row.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="more">
        <summary>More about change {number}</summary>
        <div className="stack">
          {isEdit && (
            <div>
              <p className="label">In your story now</p>
              <Json value={operation.target_before} />
            </div>
          )}
          <div>
            <p className="label">The change as stored</p>
            <Json value={operation.payload} />
          </div>
          {editable && !editing && (
            <>
              <Field
                label={`Note for change ${number} (optional)`}
                hint="Saved with your Keep or Skip decision."
              >
                {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
              </Field>
              <div>
                <Button
                  size="sm"
                  icon="edit"
                  onClick={() => {
                    setText(JSON.stringify(operation.payload, null, 2))
                    setEditing(true)
                  }}
                >
                  Edit change {number}
                </Button>
              </div>
            </>
          )}
          {editable && editing && (
            <>
              <Field
                label={`Change ${number} (JSON)`}
                hint="Saving keeps the original and any decision on it as a record, and asks for review again."
                error={problem}
              >
                {(props) => (
                  <textarea
                    {...props}
                    className="code"
                    rows={10}
                    spellCheck={false}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                  />
                )}
              </Field>
              <Field label="Reason for the edit (optional)">
                {(props) => <input {...props} value={reason} onChange={(event) => setReason(event.target.value)} />}
              </Field>
              <div className="actions">
                <Button variant="primary" disabled={busy} onClick={() => void save()}>
                  Save edited change
                </Button>
                <Button variant="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </>
          )}
        </div>
      </details>
    </li>
  )
}

/** One set of suggested changes: read it as sentences, keep or skip each, then add it to canon. */
export function ReviewDetail() {
  const { scope, canWrite, userId } = useProject()
  const { mutationId = '' } = useParams()
  const location = useLocation()
  const invalidate = useInvalidateProject(scope)
  const entries = useEntries(scope)
  const arrival = (location.state ?? {}) as {
    fromAssistant?: boolean
    understood?: string
    notes?: string[]
    discarded?: { kind: string; reason: string }[]
  }

  const detail = useQuery({
    queryKey: scopedKey(scope, 'mutation', mutationId),
    queryFn: () => api.getMutation(scope, mutationId),
  })
  const [note, setNote] = useState('')
  const [validation, setValidation] = useState<MutationValidation | null>(null)
  const [committed, setCommitted] = useState<{ revision: number } | null>(null)

  // Every review action returns the fresh change set; reload everything that depends on it.
  const action = useMutation({
    mutationFn: (run: () => Promise<Detail>) => run(),
    onSuccess: async () => {
      setValidation(null)
      await invalidate()
    },
  })
  const validate = useMutation({
    mutationFn: () => api.validateMutation(scope, mutationId),
    onSuccess: (result) => setValidation(result),
  })
  // Approval and the commit are separate, recorded steps on the server; one button
  // runs the approval when it is still needed and then the commit.
  const commit = useMutation({
    mutationFn: async (needsApproval: boolean) => {
      if (needsApproval) await api.approveMutation(scope, mutationId, note.trim() || null)
      return api.commitMutation(scope, mutationId)
    },
    // Only a success from the commit call makes this canon.
    onSuccess: async (result) => {
      setCommitted({ revision: result.revision })
      await invalidate()
    },
    onError: () => invalidate(),
  })

  const change = detail.data
  const back = (
    <Link className="crumb" to={`/projects/${scope.projectId}/review`}>
      <Icon name="arrowLeft" size={16} /> Review
    </Link>
  )
  if (!change) {
    return (
      <>
        {back}
        {detail.isPending && <Loading>Opening the suggested changes…</Loading>}
        <ErrorNotice error={detail.error} prefix="Could not open these changes" />
      </>
    )
  }

  const open = OPEN.has(change.status)
  const editable = open && canWrite
  const included = change.operations.filter((operation) => operation.decision?.decision !== 'rejected')
  const issuesFor = (ordinal: number) =>
    (validation?.issues ?? []).filter((issue) => issue.ordinal === ordinal)
  const general = issuesFor(-1)
  const busy = action.isPending || commit.isPending
  const needsApproval = change.status !== 'approved' && (change.requires_review || change.status !== 'draft')
  const canCommit = editable && !change.is_stale && included.length > 0
  const names = new Map([...entries.names, ...namesFromOperations(change.operations)])
  const groups = groupOperations(change.operations)
  // One decision for a whole group: only the changes that differ are sent, in order.
  const decideAll = (items: MutationOperation[], decision: 'accepted' | 'rejected') =>
    action.mutate(async () => {
      let latest: Detail | undefined
      for (const item of items) {
        if (item.decision?.decision !== decision) {
          latest = await api.decideOperation(scope, mutationId, item.ordinal, decision, null)
        }
      }
      return latest ?? change
    })
  const done = committed !== null || change.status === 'committed'

  return (
    <>
      {back}
      <div className="page-head">
        <div>
          <h1>{changeSetTitle(change)}</h1>
          <p className="lede">
            {plural(change.operations.length, 'suggested change')} · started{' '}
            <span title={fullDate(change.created_at)}>{timeAgo(change.created_at)}</span>
            {change.actor_user_id ? ` by ${actorLabel(change.actor_user_id, userId)}` : ''} · from{' '}
            {originLabel(change.origin)}
          </p>
        </div>
        <ChangeStateBadge status={change.status} />
      </div>

      <div className="section-gap">
        {arrival.fromAssistant && open && (
          <div className="notice" role="status">
            <p>
              <strong>Nothing has been added to your story yet.</strong>{' '}
              {arrival.understood
                ? `Here is what the assistant understood: ${arrival.understood}`
                : 'The assistant drafted the changes below.'}{' '}
              Keep the ones you want, skip the rest, then add them to canon.
            </p>
            {(arrival.notes ?? []).length > 0 && (
              <>
                <p>
                  <strong>Worth knowing before you decide:</strong>
                </p>
                <ul>
                  {(arrival.notes ?? []).map((item, index) => (
                    <li key={index}>• {item}</li>
                  ))}
                </ul>
              </>
            )}
            {(arrival.discarded ?? []).length > 0 && (
              <>
                <p>Some ideas could not be turned into valid changes and were left out:</p>
                <ul>
                  {(arrival.discarded ?? []).map((item, index) => (
                    <li key={index}>
                      {words(item.kind)}: {item.reason}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {done ? (
          <p className="notice ok" role="status">
            <span>
              <strong>
                Added to canon as version {committed?.revision ?? change.committed_revision}.
              </strong>{' '}
              These changes are now part of your story.
            </span>
            <Link className="btn" to={`/projects/${scope.projectId}`}>
              See what to fill in next
            </Link>
          </p>
        ) : change.status === 'rejected' ? (
          <p className="notice danger" role="status">
            These changes were discarded. None of them are part of your story, and they can no
            longer be changed.
          </p>
        ) : (
          <p className="notice plain" role="note">
            <span>
              <strong>Not part of your story yet.</strong>{' '}
              {change.requires_review
                ? `This needs your approval because ${change.review_reasons
                    .map((reason) => REVIEW_REASONS[reason] ?? reason)
                    .join(' and ')}.`
                : 'This is a small change by an author, so it can be added straight away.'}
            </span>
          </p>
        )}

        {change.is_stale && (
          <div className="notice warn" role="alert">
            <p>
              <strong>Your story has moved on since this was written.</strong> It was based on
              version {change.expected_revision}, and you are now on version {change.head_revision}
              , so it cannot be added as it stands. Updating keeps every change and decision, and
              asks for review again.
            </p>
            {editable && (
              <Button
                disabled={busy}
                onClick={() =>
                  action.mutate(() => api.retargetMutation(scope, mutationId, change.head_revision))
                }
              >
                Update to version {change.head_revision}
              </Button>
            )}
          </div>
        )}
        <ReadOnlyNotice canWrite={canWrite} />
        <ErrorNotice error={action.error} />

        <section aria-labelledby="changes-heading" className="stack">
          <div className="actions" style={{ justifyContent: 'space-between' }}>
            <h2 id="changes-heading">The changes</h2>
            {editable && (
              <Button size="sm" icon="shield" disabled={validate.isPending} onClick={() => validate.mutate()}>
                {validate.isPending ? 'Checking…' : 'Check for problems'}
              </Button>
            )}
          </div>
          <ErrorNotice error={validate.error} prefix="Could not check" />
          {validation && (
            <div className={`notice ${validation.ok ? 'ok' : 'danger'}`} role="status">
              <p>
                <strong>
                  {validation.ok
                    ? 'No problems found.'
                    : 'There are problems that would stop these being added.'}
                </strong>{' '}
                This is a preview; everything is checked again when you add it.
              </p>
              {general.map((issue, index) => (
                <p key={index}>{issue.message}</p>
              ))}
            </div>
          )}
          {groups.length > 1 && (
            <p className="muted small" role="status">
              {groups.map((group) => `${group.items.length} ${group.label.toLowerCase()}`).join(' · ')}
            </p>
          )}
          {groups.map((group) => {
            const kept = group.items.filter((item) => item.decision?.decision !== 'rejected').length
            return (
              <section key={group.label} aria-label={group.label} className="stack tight">
                {groups.length > 1 && (
                  <div className="actions" style={{ justifyContent: 'space-between', marginTop: '0.75rem' }}>
                    <h3 className="eyebrow">
                      {group.label} · {kept} of {group.items.length} kept
                    </h3>
                    {editable && group.items.length > 1 && (
                      <span className="actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={`Keep all ${group.label.toLowerCase()}`}
                          onClick={() => decideAll(group.items, 'accepted')}
                        >
                          Keep all
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={`Skip all ${group.label.toLowerCase()}`}
                          onClick={() => decideAll(group.items, 'rejected')}
                        >
                          Skip all
                        </Button>
                      </span>
                    )}
                  </div>
                )}
                <ol>
                  {group.items.map((operation) => (
                    <ChangeCard
                      key={`${operation.ordinal}-${operation.revision}`}
                      operation={operation}
                      title={describeOperation(operation, names)}
                      issues={issuesFor(operation.ordinal)}
                      editable={editable}
                      busy={busy}
                      onDecide={(decision, operationNote) =>
                        action.mutate(() =>
                          api.decideOperation(scope, mutationId, operation.ordinal, decision, operationNote),
                        )
                      }
                      onRevise={async (payload, reason) => {
                        // A failed edit is reported inside the editor, which keeps the text.
                        await api.reviseOperation(scope, mutationId, operation.ordinal, payload, reason)
                        setValidation(null)
                        await invalidate()
                      }}
                    />
                  ))}
                </ol>
              </section>
            )
          })}
        </section>

        {commit.isError &&
          (isStaleHead(commit.error) ? (
            <p className="notice warn" role="alert">
              <span>
                <strong>Not added.</strong> Your story changed while you were reviewing. Update
                these changes to the latest version above, then look them over again.
              </span>
            </p>
          ) : (
            <ErrorNotice error={commit.error} prefix="Not added" />
          ))}

        {editable && (
          <section className="sticky-bar" aria-label="Decision">
            <div style={{ flex: '1 1 14rem' }}>
              <strong>
                {included.length === change.operations.length
                  ? `All ${plural(included.length, 'change')} will be added`
                  : `${included.length} of ${plural(change.operations.length, 'change')} will be added`}
              </strong>
              <details className="more">
                <summary>Add a note</summary>
                <Field label="Review note (optional)">
                  {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
                </Field>
              </details>
            </div>
            <div className="actions">
              <Button
                variant="danger-quiet"
                disabled={busy}
                onClick={() =>
                  action.mutate(() => api.rejectMutation(scope, mutationId, note.trim() || null))
                }
              >
                Discard all
              </Button>
              <Button
                variant="primary"
                icon="check"
                disabled={busy || !canCommit}
                title={
                  canCommit
                    ? undefined
                    : change.is_stale
                      ? 'Update to the latest version first'
                      : 'Every change is skipped, so there is nothing to add'
                }
                onClick={() => commit.mutate(needsApproval)}
              >
                {commit.isPending ? 'Adding…' : needsApproval ? 'Approve and add to canon' : 'Add to canon'}
              </Button>
            </div>
          </section>
        )}

        <details className="more">
          <summary>Review log</summary>
          {change.transitions.length === 0 && change.decisions.length === 0 && (
            <p className="muted">No review activity yet.</p>
          )}
          <ol className="timeline">
            {change.transitions.map((transition, index) => (
              <li key={`t${index}`}>
                <strong>{(CHANGE_STATES[transition.to_status]?.label ?? transition.to_status)}</strong>{' '}
                <span className="muted">
                  {transition.actor_user_id ? `· ${actorLabel(transition.actor_user_id, userId)}` : ''}
                </span>
                <div className="when" title={fullDate(transition.created_at)}>
                  {timeAgo(transition.created_at)}
                  {transition.reason ? ` · ${transition.reason}` : ''}
                </div>
              </li>
            ))}
          </ol>
          {change.decisions.map((decision, index) => (
            <p className="small muted" key={`d${index}`}>
              {words(decision.decision)}
              {decision.actor_user_id ? ` by ${actorLabel(decision.actor_user_id, userId)}` : ''}
              {decision.note ? ` — “${decision.note}”` : ''}
            </p>
          ))}
        </details>
      </div>
    </>
  )
}
