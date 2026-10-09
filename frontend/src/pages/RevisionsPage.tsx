import { useMutation, useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type MutationCommit, type RevertProposal } from '../api/client'
import { Badge, ErrorNotice, Json, ReadOnlyNotice, WriteProblem } from '../components/ui'
import { scopedKey, useCanonWrite, useProject } from '../project'

function RevertPanel({ headRevision }: { headRevision: number }) {
  const { scope, canWrite } = useProject()
  const [from, setFrom] = useState('')
  const [proposal, setProposal] = useState<RevertProposal | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const checkboxId = useId()
  const fromId = useId()
  const fromRevision = Number(from)
  const valid = /^\d+$/.test(from) && fromRevision >= 0 && fromRevision < headRevision

  const preview = useMutation({
    mutationFn: () => api.previewRevert(scope, fromRevision, null),
    onSuccess: (result) => {
      setProposal(result)
      setAcknowledged(false)
    },
  })
  const commit = useCanonWrite<MutationCommit>(
    (expectedRevision) =>
      api.commitRevert(scope, { fromRevision, toRevision: null, expectedRevision }),
    () => setProposal(null),
  )
  const manual = proposal?.changes.filter((change) => change.inverse_operation === null) ?? []

  return (
    <section className="panel" aria-labelledby="revert-heading">
      <h2 id="revert-heading">Revert to an earlier revision</h2>
      <p className="muted">
        A revert never deletes history. It proposes new changes that undo everything after the
        revision you choose, and commits them as a new revision.
      </p>
      <div className="filters">
        <label htmlFor={fromId}>
          Return canon to revision
          <input
            id={fromId}
            inputMode="numeric"
            value={from}
            onChange={(event) => {
              setFrom(event.target.value)
              setProposal(null)
            }}
          />
        </label>
        <button
          type="button"
          className="button"
          disabled={!valid || preview.isPending}
          onClick={() => preview.mutate()}
        >
          Preview revert
        </button>
      </div>
      {from !== '' && !valid && (
        <p className="error" role="alert">
          Enter a revision between 0 and {Math.max(0, headRevision - 1)}.
        </p>
      )}
      <ErrorNotice error={preview.error} prefix="Could not preview" />

      {proposal && (
        <div role="region" aria-label="Revert preview">
          <p role="status">{proposal.summary}</p>
          {proposal.conflicts.length > 0 && (
            <div className="notice danger" role="alert">
              <p>
                <strong>This revert cannot be committed yet.</strong> Resolve these first:
              </p>
              <ul>
                {proposal.conflicts.map((conflict, index) => (
                  <li key={index}>{conflict}</li>
                ))}
              </ul>
            </div>
          )}
          {proposal.operations.length > 0 && (
            <>
              <h3>Compensating changes</h3>
              <Json value={proposal.operations} />
            </>
          )}
          {manual.length > 0 && (
            <>
              <h3>Needs manual handling</h3>
              <ul className="list">
                {manual.map((change, index) => (
                  <li key={index}>
                    r{change.revision} {change.operation_type} on {change.record_type}{' '}
                    <code>{change.record_id}</code> — {change.reason}
                  </li>
                ))}
              </ul>
            </>
          )}
          <ReadOnlyNotice canWrite={canWrite} />
          {canWrite && proposal.conflicts.length === 0 && proposal.operations.length > 0 && (
            <>
              <p className="check">
                <input
                  id={checkboxId}
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
                <label htmlFor={checkboxId}>
                  I understand this is a high-impact change to canon
                </label>
              </p>
              <button
                type="button"
                className="button danger"
                disabled={!acknowledged || commit.pending}
                onClick={() => void commit.submit()}
              >
                Commit revert
              </button>
            </>
          )}
          <WriteProblem write={commit} scope={scope} />
        </div>
      )}
    </section>
  )
}

export function RevisionsPage() {
  const { scope, headRevision } = useProject()
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions'),
    queryFn: () => api.listRevisions(scope, { limit: 100 }),
  })
  const items = revisions.data?.items ?? []

  return (
    <>
      <section className="panel" aria-labelledby="revisions-heading">
        <h2 id="revisions-heading">Revisions</h2>
        <ErrorNotice error={revisions.error} prefix="Could not load revisions" />
        {items.length === 0 && !revisions.isLoading && (
          <p className="muted">Nothing has been committed yet.</p>
        )}
        <div className="table-wrap" tabIndex={0} role="group" aria-label="Revision table, scrollable">
          <table>
            <thead>
              <tr>
                <th scope="col">Revision</th>
                <th scope="col">Origin</th>
                <th scope="col">Changes</th>
                <th scope="col">Review</th>
                <th scope="col">Evidence</th>
                <th scope="col">Committed</th>
              </tr>
            </thead>
            <tbody>
              {items.map((revision) => (
                <tr key={revision.commit_id}>
                  <th scope="row">
                    {revision.mutation_id ? (
                      <Link to={`/projects/${scope.projectId}/mutations/${revision.mutation_id}`}>
                        r{revision.revision}
                      </Link>
                    ) : (
                      `r${revision.revision}`
                    )}
                  </th>
                  <td>
                    {revision.origin ?? 'unknown'}{' '}
                    {revision.intent === 'revert' && <Badge tone="warn">revert</Badge>}
                    {revision.actor_user_id && <span className="muted"> · {revision.actor_user_id}</span>}
                  </td>
                  <td>{revision.operation_count}</td>
                  <td>{revision.decision_count}</td>
                  <td>{revision.evidence_count}</td>
                  <td>{new Date(revision.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <RevertPanel headRevision={headRevision} />
    </>
  )
}
