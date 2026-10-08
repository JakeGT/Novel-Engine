import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ProjectContext } from './ProjectLayout'

export function EntityDetail() {
  const { scope, headRevision } = useOutletContext<ProjectContext>()
  const { entityId = '' } = useParams()
  const queryClient = useQueryClient()

  const entity = useQuery({
    queryKey: ['entity', scope.workspaceId, scope.projectId, entityId],
    queryFn: () => api.getEntity(scope, entityId),
  })

  const history = useQuery({
    queryKey: ['entity-history', scope.workspaceId, scope.projectId, entityId],
    queryFn: () => api.entityHistory(scope, entityId),
  })

  const retire = useMutation({
    mutationFn: () => api.retireEntity(scope, entityId, headRevision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['entity'] })
      queryClient.invalidateQueries({ queryKey: ['entity-history'] })
      queryClient.invalidateQueries({ queryKey: ['entities'] })
      queryClient.invalidateQueries({ queryKey: ['projection'] })
    },
  })

  return (
    <section className="panel">
      <Link to={`/projects/${scope.projectId}`}>← Entities</Link>

      {entity.isLoading && <p className="muted">Loading entity…</p>}
      {entity.isError && <p className="error">{(entity.error as Error).message}</p>}

      {entity.data && (
        <>
          <h2>
            {entity.data.name} <span className="muted">{entity.data.type}</span>{' '}
            <span className="badge">{entity.data.lifecycle}</span>
          </h2>
          <dl>
            <dt>ID</dt>
            <dd>{entity.data.id}</dd>
            <dt>Created revision</dt>
            <dd>r{entity.data.created_revision}</dd>
            {entity.data.retired_revision != null && (
              <>
                <dt>Retired revision</dt>
                <dd>r{entity.data.retired_revision}</dd>
              </>
            )}
            <dt>Schema version</dt>
            <dd>{entity.data.schema_version_id}</dd>
            <dt>Attributes</dt>
            <dd>
              <pre>{JSON.stringify(entity.data.attributes ?? {}, null, 2)}</pre>
            </dd>
          </dl>

          {entity.data.lifecycle === 'active' && (
            <button
              className="button"
              onClick={() => retire.mutate()}
              disabled={retire.isPending}
            >
              Retire
            </button>
          )}
          {retire.isError && <p className="error">{(retire.error as Error).message}</p>}
        </>
      )}

      <h3>History</h3>
      <ul className="list">
        {(history.data?.items ?? []).map((entry) => (
          <li key={`${entry.revision}-${entry.record_id}-${entry.operation_type}`}>
            <span className="badge">r{entry.revision}</span> {entry.operation_type}{' '}
            <span className="muted">{entry.commit_id}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
