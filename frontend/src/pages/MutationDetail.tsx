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
import { Badge, ErrorNotice, Field, Json, MutationStateBadge, ReadOnlyNotice } from '../components/ui'
import { scopedKey, useInvalidateProject, useProject } from '../project'

const REASONS: Record<string, string> = {
  protected_origin: 'it did not come directly from an author',
  destructive_operation: 'it retires a record',
  bulk_change: 'it changes many records at once',
  conflict_resolution: 'it reverts earlier history',
}

const OPEN = new Set(['draft', 'validating', 'needs_review', 'approved'])

type Issue = MutationValidation['issues'][number]

function changedKeys(operation: MutationOperation): string[] {
  const before = operation.target_before ?? {}
  return Object.keys(operation.payload).filter(
    (key) =>
      !['op', 'id', 'evidence'].includes(key) &&
      operation.payload[key] !== null &&
      key in before &&
      JSON.stringify(before[key]) !== JSON.stringify(operation.payload[key]),
  )
}

function OperationCard({
  operation,
  issues,
  editable,
  busy,
  onDecide,
  onRevise,
}: {
  operation: MutationOperation
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
  const before = operation.target_before
  const changed = changedKeys(operation)

  const save = async () => {
    let payload: unknown
    try {
      payload = JSON.parse(text)
    } catch (failure) {
      return setProblem(`Not valid JSON: ${errorMessage(failure)}`)
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return setProblem('The operation must be a JSON object.')
    }
    try {
      await onRevise(payload as Record<string, unknown>, reason.trim() || null)
      setProblem(null)
      setEditing(false)
    } catch (failure) {
      // Keep the editor open with the author's text.
      setProblem(errorMessage(failure))
    }
  }

  return (
    <li className={`operation ${decision === 'rejected' ? 'rejected' : ''}`}>
      <h4>
        Operation {number}: {operation.operation_type}{' '}
        {operation.revision > 1 && <Badge tone="info">Edited · revision {operation.revision}</Badge>}{' '}
        {decision === 'accepted' && <Badge tone="ok">Accepted</Badge>}
        {decision === 'rejected' && <Badge tone="danger">Rejected · will not be committed</Badge>}
        {!decision && <Badge>Undecided · included</Badge>}
      </h4>
      {operation.decision?.note && <p className="muted">Reviewer note: {operation.decision.note}</p>}

      {issues.map((issue, index) => (
        <p
          key={index}
          className={issue.severity === 'blocking' ? 'error' : 'notice'}
          role={issue.severity === 'blocking' ? 'alert' : 'note'}
        >
          <strong>{issue.severity === 'blocking' ? 'Blocks commit' : 'Advisory'}:</strong>{' '}
          {issue.message} <span className="muted">({issue.code})</span>
        </p>
      ))}

      {before && Object.keys(before).length > 0 ? (
        <div className="diff">
          <div>
            <p className="muted">Canon now</p>
            <Json value={before} />
          </div>
          <div>
            <p className="muted">
              Proposed change{changed.length > 0 ? ` (changes ${changed.join(', ')})` : ''}
            </p>
            <Json value={operation.payload} />
          </div>
        </div>
      ) : (
        <div>
          <p className="muted">{before ? 'Target no longer exists in canon. Proposed:' : 'New record'}</p>
          <Json value={operation.payload} />
        </div>
      )}

      {editable && !editing && (
        <div className="stack">
          <Field label={`Note for operation ${number} (optional)`}>
            {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
          </Field>
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => onDecide('accepted', note.trim() || null)}
            >
              Accept operation {number}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => onDecide('rejected', note.trim() || null)}
            >
              Reject operation {number}
            </button>
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setText(JSON.stringify(operation.payload, null, 2))
                setEditing(true)
              }}
            >
              Edit operation {number}
            </button>
          </div>
        </div>
      )}
      {editable && editing && (
        <div className="stack">
          <Field
            label={`Operation ${number} (JSON)`}
            hint="Saving creates a new revision; the original and any decision on it are kept as review evidence."
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
            <button type="button" className="button" disabled={busy} onClick={() => void save()}>
              Save as new revision
            </button>
            <button type="button" className="button secondary" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

export function MutationDetail() {
  const { scope, canWrite } = useProject()
  const { mutationId = '' } = useParams()
  const location = useLocation()
  const invalidate = useInvalidateProject(scope)
  const arrival = (location.state ?? {}) as {
    fromGuidedWorkflow?: boolean
    discarded?: { kind: string; reason: string }[]
  }

  const detail = useQuery({
    queryKey: scopedKey(scope, 'mutation', mutationId),
    queryFn: () => api.getMutation(scope, mutationId),
  })
  const [note, setNote] = useState('')
  const [validation, setValidation] = useState<MutationValidation | null>(null)
  const [committed, setCommitted] = useState<{ revision: number } | null>(null)

  // Every review action returns the fresh proposal; reload everything that depends on it.
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
  const commit = useMutation({
    mutationFn: () => api.commitMutation(scope, mutationId),
    // Only a 200 from the commit call makes this canon.
    onSuccess: async (result) => {
      setCommitted({ revision: result.revision })
      await invalidate()
    },
    onError: () => invalidate(),
  })

  const mutation = detail.data
  if (!mutation) {
    return (
      <section className="panel">
        {detail.isLoading && (
          <p className="muted" role="status">
            Loading proposal…
          </p>
        )}
        <ErrorNotice error={detail.error} prefix="Could not load the proposal" />
      </section>
    )
  }

  const open = OPEN.has(mutation.status)
  const editable = open && canWrite
  const included = mutation.operations.filter((operation) => operation.decision?.decision !== 'rejected')
  const issuesFor = (ordinal: number) =>
    (validation?.issues ?? []).filter((issue) => issue.ordinal === ordinal)
  const general = issuesFor(-1)
  const busy = action.isPending || commit.isPending
  const canCommit =
    editable &&
    !mutation.is_stale &&
    (mutation.status === 'approved' || (mutation.status === 'draft' && !mutation.requires_review))

  return (
    <>
      <p>
        <Link to={`/projects/${scope.projectId}/mutations`}>← Proposals</Link>
      </p>
      <section className="panel" aria-labelledby="proposal-heading">
        <h2 id="proposal-heading">
          {mutation.intent === 'revert' ? 'Revert proposal' : 'Proposal'}{' '}
          <MutationStateBadge status={mutation.status} />
        </h2>

        {arrival.fromGuidedWorkflow && open && (
          <div className="notice" role="status">
            <p>
              <strong>Nothing has been saved to canon.</strong> The guided interview produced this
              proposal. Review each change below, then approve and commit what you want to keep.
            </p>
            {(arrival.discarded ?? []).length > 0 && (
              <>
                <p>Some suggestions could not be turned into valid changes and were left out:</p>
                <ul>
                  {(arrival.discarded ?? []).map((item, index) => (
                    <li key={index}>
                      {item.kind}: {item.reason}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {committed || mutation.status === 'committed' ? (
          <p className="notice ok" role="status">
            <strong>
              Committed to canon as revision r{committed?.revision ?? mutation.committed_revision}.
            </strong>
          </p>
        ) : mutation.status === 'rejected' ? (
          <p className="notice danger" role="status">
            This proposal was rejected. None of it is canon, and it can no longer be changed.
          </p>
        ) : (
          <p className="notice" role="note">
            Not canon yet. {included.length} of {mutation.operations.length} operations would be
            committed.
          </p>
        )}

        <dl>
          <dt>Origin</dt>
          <dd>{mutation.origin}</dd>
          <dt>Intent</dt>
          <dd>{mutation.intent}</dd>
          <dt>Proposed against</dt>
          <dd>r{mutation.expected_revision}</dd>
          <dt>Canon head now</dt>
          <dd>r{mutation.head_revision}</dd>
          <dt>Review</dt>
          <dd>
            {mutation.requires_review
              ? `An explicit approval is required because ${mutation.review_reasons
                  .map((reason) => REASONS[reason] ?? reason)
                  .join(' and ')}.`
              : 'A low-impact author change; it may be committed directly.'}
          </dd>
        </dl>

        {mutation.is_stale && (
          <div className="notice warn" role="alert">
            <p>
              <strong>This proposal is out of date.</strong> It was written against r
              {mutation.expected_revision}, but canon is now at r{mutation.head_revision}. It cannot
              be committed as it stands. Retargeting keeps every operation and decision, and asks
              for review again.
            </p>
            {editable && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() =>
                  action.mutate(() => api.retargetMutation(scope, mutationId, mutation.head_revision))
                }
              >
                Retarget to r{mutation.head_revision}
              </button>
            )}
          </div>
        )}
        <ReadOnlyNotice canWrite={canWrite} />
        <ErrorNotice error={action.error} />
      </section>

      <section className="panel" aria-labelledby="operations-heading">
        <h3 id="operations-heading">Operations</h3>
        {editable && (
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              disabled={validate.isPending}
              onClick={() => validate.mutate()}
            >
              Check against canon
            </button>
          </div>
        )}
        <ErrorNotice error={validate.error} prefix="Could not validate" />
        {validation && (
          <div className={`notice ${validation.ok ? 'ok' : 'danger'}`} role="status">
            <p>
              <strong>
                {validation.ok
                  ? 'No blocking problems found.'
                  : 'There are problems that would block a commit.'}
              </strong>{' '}
              This check is a preview; the commit checks again.
            </p>
            {general.map((issue, index) => (
              <p key={index} className={issue.severity === 'blocking' ? 'error' : ''}>
                {issue.message} <span className="muted">({issue.code})</span>
              </p>
            ))}
          </div>
        )}
        <ol className="operations">
          {mutation.operations.map((operation) => (
            <OperationCard
              key={`${operation.ordinal}-${operation.revision}`}
              operation={operation}
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

      {editable && (
        <section className="panel" aria-labelledby="decision-heading">
          <h3 id="decision-heading">Decision</h3>
          <Field label="Review note (optional)">
            {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
          </Field>
          <div className="actions">
            {mutation.status !== 'approved' && (
              <button
                type="button"
                className="button"
                disabled={busy || included.length === 0}
                onClick={() =>
                  action.mutate(() => api.approveMutation(scope, mutationId, note.trim() || null))
                }
              >
                Approve
              </button>
            )}
            <button
              type="button"
              className="button"
              disabled={busy || !canCommit}
              title={
                canCommit
                  ? undefined
                  : mutation.is_stale
                    ? 'Retarget the proposal first'
                    : 'Approve the proposal first'
              }
              onClick={() => commit.mutate()}
            >
              {commit.isPending ? 'Committing…' : 'Commit to canon'}
            </button>
            <button
              type="button"
              className="button danger"
              disabled={busy}
              onClick={() =>
                action.mutate(() => api.rejectMutation(scope, mutationId, note.trim() || null))
              }
            >
              Reject proposal
            </button>
          </div>
          {included.length === 0 && (
            <p className="muted">Every operation is rejected, so there is nothing to approve.</p>
          )}
          {commit.isError &&
            (isStaleHead(commit.error) ? (
              <p className="notice warn" role="alert">
                <strong>Not committed.</strong> Canon moved on while you were reviewing. Retarget the
                proposal above and review it against the new state.
              </p>
            ) : (
              <ErrorNotice error={commit.error} prefix="Not committed" />
            ))}
        </section>
      )}

      <section className="panel" aria-labelledby="log-heading">
        <h3 id="log-heading">Review log</h3>
        {mutation.transitions.length === 0 && mutation.decisions.length === 0 && (
          <p className="muted">No review activity yet.</p>
        )}
        <ol className="list timeline">
          {mutation.transitions.map((transition, index) => (
            <li key={`t${index}`}>
              <MutationStateBadge status={transition.from_status} /> →{' '}
              <MutationStateBadge status={transition.to_status} />{' '}
              <span className="muted">
                {transition.reason}
                {transition.actor_user_id ? ` · ${transition.actor_user_id}` : ''} ·{' '}
                {new Date(transition.created_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ol>
        {mutation.decisions.length > 0 && (
          <>
            <h4>Decisions on the whole proposal</h4>
            <ul className="list">
              {mutation.decisions.map((decision, index) => (
                <li key={`d${index}`}>
                  <strong>{decision.decision}</strong>
                  {decision.actor_user_id ? ` by ${decision.actor_user_id}` : ''}
                  {decision.note ? ` — ${decision.note}` : ''}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  )
}
