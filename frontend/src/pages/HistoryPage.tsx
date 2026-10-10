import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type MutationCommit, type RevisionSummary } from '../api/client'
import { Badge, Button, Dialog, EmptyState, ErrorNotice, Json, Loading, WriteProblem } from '../components/ui'
import { dayLabel, fullDate } from '../lib/time'
import { describeOperation, namesFromOperations, originLabel, plural, words } from '../lib/words'
import { actorLabel, scopedKey, useCanonWrite, useEntries, useProject } from '../project'

/**
 * Going back never deletes history: it works out the changes that undo everything
 * after the chosen version and saves them as a new version.
 */
function RestoreDialog({ revision, onClose }: { revision: number; onClose: () => void }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const [acknowledged, setAcknowledged] = useState(false)
  const checkboxId = useId()

  const preview = useMutation({ mutationFn: () => api.previewRevert(scope, revision, null) })
  const { mutate: loadPreview } = preview
  useEffect(() => loadPreview(), [loadPreview])

  const restore = useCanonWrite<MutationCommit>(
    (expectedRevision) =>
      api.commitRevert(scope, { fromRevision: revision, toRevision: null, expectedRevision }),
    onClose,
  )
  const proposal = preview.data
  const operations = (proposal?.operations ?? []).map((payload) => ({
    operation_type: String(payload.op ?? ''),
    payload,
  }))
  const names = new Map([...entries.names, ...namesFromOperations(operations)])
  const manual = proposal?.changes.filter((change) => change.inverse_operation === null) ?? []
  const possible = Boolean(proposal) && proposal?.conflicts.length === 0 && operations.length > 0

  return (
    <Dialog title={`Go back to version ${revision}?`} onClose={onClose} wide>
      <div className="stack">
        <p className="muted">
          Nothing is deleted. The changes below undo everything after version {revision}, and are
          saved as a new version — so you can always come forward again.
        </p>
        {preview.isPending && <Loading>Working out what would change…</Loading>}
        <ErrorNotice error={preview.error} prefix="Could not work out the changes" />

        {proposal && (
          <div className="stack" role="region" aria-label="What going back would do">
            <p role="status">
              <strong>
                {operations.length === 0
                  ? 'There is nothing to undo.'
                  : `Going back would make ${plural(operations.length, 'change')}.`}
              </strong>
            </p>
            {proposal.conflicts.length > 0 && (
              <div className="notice danger" role="alert">
                <p>
                  <strong>This version can’t be restored yet.</strong> Sort these out first:
                </p>
                <ul>
                  {proposal.conflicts.map((conflict, index) => (
                    <li key={index}>• {conflict}</li>
                  ))}
                </ul>
              </div>
            )}
            {operations.length > 0 && (
              <div>
                <p className="label">What would be undone</p>
                <ul className="rows" style={{ marginTop: '0.5rem' }}>
                  {operations.map((operation, index) => (
                    <li className="row" key={index}>
                      {describeOperation(operation, names)}
                    </li>
                  ))}
                </ul>
                <details className="more" style={{ marginTop: '0.75rem' }}>
                  <summary>Show the stored changes</summary>
                  <Json value={proposal.operations} />
                </details>
              </div>
            )}
            {manual.length > 0 && (
              <div className="notice warn" role="note">
                <p>
                  <strong>These can’t be undone automatically</strong> and would need fixing by hand:
                </p>
                <ul>
                  {manual.map((change, index) => (
                    <li key={index}>
                      • Version {change.revision}: {words(change.operation_type).toLowerCase()} —{' '}
                      {change.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!canWrite && (
              <p className="notice plain" role="note">
                You have read-only access, so you can look at this but not restore it.
              </p>
            )}
            {canWrite && possible && (
              <p className="check">
                <input
                  id={checkboxId}
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
                <label htmlFor={checkboxId}>I understand this changes a lot of my story at once</label>
              </p>
            )}
            <WriteProblem write={restore} scope={scope} />
          </div>
        )}

        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {canWrite && possible && (
            <Button
              variant="danger"
              icon="undo"
              disabled={!acknowledged || restore.pending}
              onClick={() => void restore.submit()}
            >
              {restore.pending ? 'Restoring…' : `Go back to version ${revision}`}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/** Every saved version of the story bible, newest first, with a way back to any of them. */
export function HistoryPage() {
  const { scope, headRevision, canWrite, userId } = useProject()
  const [restoring, setRestoring] = useState<number | null>(null)
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions'),
    queryFn: () => api.listRevisions(scope, { limit: 100 }),
  })
  const items = revisions.data?.items ?? []

  const days: { label: string; items: RevisionSummary[] }[] = []
  for (const revision of items) {
    const label = dayLabel(revision.created_at)
    const last = days[days.length - 1]
    if (last?.label === label) last.items.push(revision)
    else days.push({ label, items: [revision] })
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p className="lede">
            Every time something is added to canon, a new version is saved. Nothing is ever lost:
            you can see what changed and go back to any earlier version.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <ErrorNotice error={revisions.error} prefix="Could not load the history" />
        {revisions.isPending && <Loading>Loading history…</Loading>}
        {revisions.isSuccess && items.length === 0 && (
          <EmptyState icon="clock" title="No versions yet">
            The first thing you add to your story bible will be saved as version 1.
          </EmptyState>
        )}

        {days.map((day) => (
          <section key={day.label} aria-label={day.label} className="card">
            <div className="card-head">
              <h2 className="eyebrow">{day.label}</h2>
            </div>
            <ol className="rows">
              {day.items.map((revision) => {
                const who =
                  revision.origin && revision.origin !== 'author'
                    ? originLabel(revision.origin)
                    : actorLabel(revision.actor_user_id, userId) || 'an author'
                const current = revision.revision === headRevision
                return (
                  <li className="row" key={revision.commit_id}>
                    <span className="grow">
                      <strong>Version {revision.revision}</strong>{' '}
                      {current && <Badge tone="ok">Current</Badge>}{' '}
                      {revision.intent === 'revert' && <Badge tone="warn">Went back</Badge>}
                      <span className="muted small" style={{ display: 'block' }}>
                        {plural(revision.operation_count, 'change')} by {who}
                        {revision.decision_count > 0 ? ' · reviewed' : ''} ·{' '}
                        <span title={fullDate(revision.created_at)}>
                          {new Date(revision.created_at).toLocaleTimeString(undefined, { timeStyle: 'short' })}
                        </span>
                      </span>
                    </span>
                    {revision.mutation_id && (
                      <Link
                        className="btn sm ghost"
                        to={`/projects/${scope.projectId}/review/${revision.mutation_id}`}
                        aria-label={`See what changed in version ${revision.revision}`}
                      >
                        See changes
                      </Link>
                    )}
                    {canWrite && !current && (
                      <Button
                        size="sm"
                        icon="undo"
                        aria-label={`Go back to version ${revision.revision}`}
                        onClick={() => setRestoring(revision.revision)}
                      >
                        Go back to this
                      </Button>
                    )}
                  </li>
                )
              })}
            </ol>
          </section>
        ))}

        {canWrite && items.length > 0 && headRevision > 0 && (
          <p className="muted small">
            Want an empty story bible again?{' '}
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setRestoring(0)}
            >
              Go back to the very beginning
            </button>
          </p>
        )}
      </div>

      {restoring !== null && <RestoreDialog revision={restoring} onClose={() => setRestoring(null)} />}
    </>
  )
}
