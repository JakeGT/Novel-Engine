import { useQuery } from '@tanstack/react-query'
import { Link, Outlet, useParams } from 'react-router-dom'
import { api, type Scope } from '../api/client'
import { getActiveWorkspaceId } from '../scope'

export interface ProjectContext {
  scope: Scope
  headRevision: number
}

export function ProjectLayout() {
  const { projectId = '' } = useParams()
  const scope: Scope = { workspaceId: getActiveWorkspaceId(), projectId }

  const projection = useQuery({
    queryKey: ['projection', scope.workspaceId, projectId],
    queryFn: () => api.projection(scope),
  })

  const status = projection.data
  const headRevision = status?.head_revision ?? 0

  return (
    <main className="shell">
      <header className="row">
        <div>
          <h1>Novel Engine</h1>
          <p className="tagline">Project {projectId}</p>
        </div>
        <nav className="row">
          <Link to={`/projects/${projectId}`}>Entities</Link>
          <Link to="/">Switch project</Link>
        </nav>
      </header>

      <section className="panel status" aria-live="polite">
        {projection.isSuccess && status ? (
          <>
            <span>
              Canon head <strong>r{status.head_revision}</strong> · graph applied{' '}
              <strong>r{status.applied_revision}</strong>
            </span>
            <span className={status.current ? 'badge ok' : 'badge warn'}>
              {status.current ? 'Graph current' : `Projection lag ${status.lag}`}
            </span>
            {status.failed_deliveries > 0 && (
              <span className="badge warn">Failed deliveries: {status.failed_deliveries}</span>
            )}
            {status.last_error && <span className="badge warn">Last error: {status.last_error}</span>}
          </>
        ) : (
          <span className="muted">Loading project status…</span>
        )}
      </section>

      <Outlet context={{ scope, headRevision } satisfies ProjectContext} />
    </main>
  )
}
