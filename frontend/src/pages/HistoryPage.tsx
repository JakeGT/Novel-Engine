import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RestoreDialog } from '../components/RestoreDialog'
import { api, type MutationCommit, type RevisionSummary } from '../api/client'
import { Badge, Button, Dialog, EmptyState, ErrorNotice, Json, Loading, WriteProblem } from '../components/ui'
import { dayLabel, fullDate } from '../lib/time'
import { originLabel, plural } from '../lib/words'
import { actorLabel, scopedKey, useProject } from '../project'

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
