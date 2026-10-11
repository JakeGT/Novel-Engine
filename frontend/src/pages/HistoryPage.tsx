import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RestoreDialog } from '../components/RestoreDialog'
import { api, type MutationCommit, type RevisionSummary, type VersionName } from '../api/client'
import { Badge, Button, Dialog, EmptyState, ErrorNotice, Json, Loading, WriteProblem } from '../components/ui'
import { dayLabel, fullDate } from '../lib/time'
import { describeOperation, originLabel, plural } from '../lib/words'
import { actorLabel, scopedKey, useEntries, useProject } from '../project'

/** Every saved version of the story bible, newest first, with a way back to any of them. */
export function HistoryPage() {
  const { scope, headRevision, canWrite, userId } = useProject()
  const [restoring, setRestoring] = useState<number | null>(null)
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions'),
    queryFn: () => api.listRevisions(scope, { limit: 100 }),
  })
  const items = revisions.data?.items ?? []
  const names = useQuery({ queryKey: scopedKey(scope, 'version-names'), queryFn: () => api.versionNames(scope) })
  const namesFor = (revision: number) => (names.data?.items ?? []).filter((item) => item.revision === revision)
  const [naming, setNaming] = useState<number | null>(null)
  const [since, setSince] = useState<number | null>(null)

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
                      {namesFor(revision.revision).map((item) => (
                        <span key={item.name}>
                          {' '}
                          <Badge tone="accent">{item.name}</Badge>
                        </span>
                      ))}
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
                    {!current && (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`What changed since version ${revision.revision}`}
                        onClick={() => setSince(revision.revision)}
                      >
                        Changes since
                      </Button>
                    )}
                    {canWrite && (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Name version ${revision.revision}`}
                        onClick={() => setNaming(revision.revision)}
                      >
                        Name it
                      </Button>
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
      {naming !== null && (
        <NameVersion revision={naming} current={namesFor(naming)} onClose={() => setNaming(null)} />
      )}
      {since !== null && <ChangesSince revision={since} head={headRevision} onClose={() => setSince(null)} />}
    </>
  )
}

/** A name for a version, so it can be found again: "First draft done". */
function NameVersion({
  revision,
  current,
  onClose,
}: {
  revision: number
  current: VersionName[]
  onClose: () => void
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const [name, setName] = useState('')
  const refresh = () => client.invalidateQueries({ queryKey: scopedKey(scope, 'version-names') })
  const add = useMutation({
    mutationFn: () => api.nameVersion(scope, name.trim(), revision),
    onSuccess: async () => {
      await refresh()
      onClose()
    },
  })
  const remove = useMutation({
    mutationFn: (existing: string) => api.removeVersionName(scope, existing),
    onSuccess: refresh,
  })
  return (
    <Dialog title={`Name version ${revision}`} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (name.trim()) add.mutate()
        }}
      >
        <div className="field">
          <label htmlFor="version-name">A name to find it by</label>
          <input
            id="version-name"
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
            aria-describedby="version-name-hint"
            autoFocus
          />
          <span id="version-name-hint" className="hint">
            For example “First draft done”. A name you have used before moves to this version.
          </span>
        </div>
        {current.length > 0 && (
          <ul className="rows">
            {current.map((item) => (
              <li className="row" key={item.name}>
                <span className="grow">{item.name}</span>
                <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(item.name)}>
                  Remove this name
                </Button>
              </li>
            ))}
          </ul>
        )}
        <ErrorNotice error={add.error ?? remove.error} prefix="Could not save the name" />
        <div className="actions end">
          <Button onClick={onClose}>Close</Button>
          <Button type="submit" variant="primary" disabled={!name.trim() || add.isPending}>
            Name it
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Everything saved after a version, in order, in ordinary words. */
function ChangesSince({ revision, head, onClose }: { revision: number; head: number; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const changes = useQuery({
    queryKey: scopedKey(scope, 'changes-since', revision, head),
    queryFn: () => api.changesBetween(scope, revision, head),
  })
  const items = changes.data?.items ?? []
  return (
    <Dialog title={`What changed since version ${revision}`} onClose={onClose} wide>
      <div className="stack">
        {changes.isPending && <Loading>Comparing…</Loading>}
        <ErrorNotice error={changes.error} prefix="Could not compare the versions" />
        {changes.isSuccess && items.length === 0 && <p className="muted">Nothing has changed since then.</p>}
        {items.length > 0 && (
          <>
            <p className="muted">
              {plural(items.length, 'change')} between version {revision} and version {head}, oldest first.
            </p>
            <ol className="rows">
              {items.map((item, index) => (
                <li className="row" key={index}>
                  <span className="grow">
                    {describeOperation(
                      { operation_type: item.operation_type, payload: { ...item.change, id: item.record_id } },
                      entries.names,
                    )}
                  </span>
                  <span className="muted small">Version {item.revision}</span>
                </li>
              ))}
            </ol>
          </>
        )}
        <div className="actions end">
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    </Dialog>
  )
}
