import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { ErrorNotice, MutationStateBadge } from '../components/ui'
import { scopedKey, useProject } from '../project'

const FILTERS: { value: string; label: string }[] = [
  { value: 'needs_review', label: 'Awaiting review' },
  { value: 'draft', label: 'Drafts' },
  { value: 'approved', label: 'Approved, not committed' },
  { value: 'committed', label: 'Committed' },
  { value: 'rejected', label: 'Rejected' },
  { value: '', label: 'All' },
]

export function MutationQueue() {
  const { scope } = useProject()
  const [status, setStatus] = useState('needs_review')
  const mutations = useQuery({
    queryKey: scopedKey(scope, 'mutations', status),
    queryFn: () => api.listMutations(scope, { status: status || undefined, limit: 100 }),
    // Proposals arrive from other people and from model runs: always show the live queue.
    refetchOnMount: 'always',
  })
  const items = mutations.data?.items ?? []

  return (
    <section className="panel" aria-labelledby="queue-heading">
      <h2 id="queue-heading">Proposals</h2>
      <p className="muted">
        A proposal is a set of changes that is not canon yet. Proposals from a model or an import,
        and destructive or bulk changes, need an explicit decision before they can be committed.
      </p>
      <label className="inline">
        Show
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          {FILTERS.map((filter) => (
            <option key={filter.value} value={filter.value}>
              {filter.label}
            </option>
          ))}
        </select>
      </label>
      <ErrorNotice error={mutations.error} prefix="Could not load proposals" />
      <p className="muted" role="status">
        {mutations.isLoading ? 'Loading…' : `${items.length} proposal${items.length === 1 ? '' : 's'}`}
      </p>
      <ul className="list">
        {items.map((mutation) => (
          <li key={mutation.id}>
            <Link to={`/projects/${scope.projectId}/mutations/${mutation.id}`}>
              {mutation.intent === 'revert' ? 'Revert' : 'Change'} from {mutation.origin}
            </Link>{' '}
            <MutationStateBadge status={mutation.status} />{' '}
            <span className="muted">
              against r{mutation.expected_revision} · {new Date(mutation.created_at).toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
