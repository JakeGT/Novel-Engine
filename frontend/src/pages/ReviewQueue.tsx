import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { ChangeStateBadge, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { fullDate, timeAgo } from '../lib/time'
import { changeSetTitle, isOpenChange, plural } from '../lib/words'
import { actorLabel, scopedKey, useProject } from '../project'

const FILTERS: { value: string; label: string }[] = [
  { value: 'open', label: 'Waiting for you' },
  { value: 'committed', label: 'In your story' },
  { value: 'rejected', label: 'Discarded' },
  { value: '', label: 'Everything' },
]

/** The inbox of suggested changes: everything that is not canon until someone says so. */
export function ReviewQueue() {
  const { scope, userId } = useProject()
  const [status, setStatus] = useState('open')
  const changes = useQuery({
    queryKey: scopedKey(scope, 'mutations', status),
    // "Waiting" spans several server states, so that view filters the full list here.
    queryFn: () =>
      api.listMutations(scope, { status: status === 'open' ? undefined : status || undefined, limit: 100 }),
    // Suggestions arrive from other people and from the assistant: always show the live inbox.
    refetchOnMount: 'always',
  })
  const items = (changes.data?.items ?? []).filter(
    (item) => status !== 'open' || isOpenChange(item.status),
  )

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Review</h1>
          <p className="lede">
            Suggested changes wait here until you decide. Anything from the AI assistant or an
            import, and anything that archives or changes a lot at once, needs your say-so before it
            becomes part of your story.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <div className="chips" role="group" aria-label="Show">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              className="chip"
              aria-pressed={status === filter.value}
              onClick={() => setStatus(filter.value)}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <ErrorNotice error={changes.error} prefix="Could not load suggested changes" />
        {changes.isPending && <Loading>Loading…</Loading>}

        {changes.isSuccess && items.length === 0 && (
          <EmptyState icon="check" title={status === 'open' ? 'You’re all caught up' : 'Nothing here'}>
            {status === 'open'
              ? 'No suggested changes are waiting for you.'
              : 'No changes are in this state.'}
          </EmptyState>
        )}

        {items.length > 0 && (
          <>
            <p className="muted small" role="status">
              {plural(items.length, 'set of changes', 'sets of changes')}
            </p>
            <ul className="card flush">
              {items.map((change) => {
                const who = actorLabel(change.actor_user_id, userId)
                return (
                  <li
                    className="row"
                    key={change.id}
                    style={{ padding: '0.9rem 1.25rem' }}
                  >
                    <span className="grow">
                      <Link to={`/projects/${scope.projectId}/review/${change.id}`}>
                        <strong>{changeSetTitle(change)}</strong>
                      </Link>
                      <span className="muted small" style={{ display: 'block' }} title={fullDate(change.created_at)}>
                        {who ? `Started by ${who} · ` : ''}
                        {timeAgo(change.created_at)} · based on version {change.expected_revision}
                      </span>
                    </span>
                    <ChangeStateBadge status={change.status} />
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </div>
    </>
  )
}
