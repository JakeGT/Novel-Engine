import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import { api, type Scope } from '../api/client'
import { Badge } from '../components/ui'
import { scopedKey, type ProjectContext } from '../project'
import { getActiveWorkspaceId } from '../scope'

const IMPORT_TEXT: Record<string, string> = {
  importing: 'An archive import is in progress. What you see here is incomplete until it finishes.',
  failed:
    'An archive import into this project failed part-way. This project is not a complete restore: an owner should retry the import or remove it.',
  removed: 'A failed archive import was removed from this project.',
}

export function ProjectLayout() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const workspaceId = getActiveWorkspaceId()
  const scope: Scope = { workspaceId, projectId }

  const projection = useQuery({
    queryKey: scopedKey(scope, 'projection'),
    queryFn: () => api.projection(scope),
  })
  const access = useQuery({
    queryKey: scopedKey(scope, 'access'),
    queryFn: () => api.access(scope),
  })
  // Hosted workspaces can hold several projects; local mode has exactly one.
  const projects = useQuery({
    queryKey: ['projects', workspaceId],
    queryFn: () => api.listProjects(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
  const pending = useQuery({
    queryKey: scopedKey(scope, 'mutations', 'needs_review'),
    queryFn: () => api.listMutations(scope, { status: 'needs_review', limit: 100 }),
    refetchInterval: 30_000,
  })

  const status = projection.data
  const context: ProjectContext = {
    scope,
    headRevision: status?.head_revision ?? 0,
    refreshHead: async () => {
      const fresh = await projection.refetch()
      if (fresh.data === undefined) throw fresh.error ?? new Error('Could not reload the head.')
      return fresh.data.head_revision
    },
    access: access.data,
    canWrite: access.data?.can_write ?? false,
    canAdmin: access.data?.can_admin ?? false,
  }
  const projectName = projects.data?.find((item) => item.id === projectId)?.name ?? projectId
  const base = `/projects/${projectId}`
  const awaiting = pending.data?.items.length ?? 0
  const importState = status?.import

  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="row">
        <div>
          <p className="brand">Novel Engine</p>
          <p className="tagline">
            Project <strong>{projectName}</strong>
          </p>
        </div>
        <div className="row">
          {projects.data && projects.data.length > 1 && (
            <label className="switcher">
              Project
              <select value={projectId} onChange={(event) => navigate(`/projects/${event.target.value}`)}>
                {projects.data.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <NavLink to="/">Switch workspace</NavLink>
        </div>
      </header>

      <nav aria-label="Project">
        <ul className="tabs">
          <li>
            <NavLink to={base} end>
              Entities
            </NavLink>
          </li>
          <li>
            <NavLink to={`${base}/mutations`}>
              Review{awaiting > 0 ? ` (${awaiting})` : ''}
            </NavLink>
          </li>
          <li>
            <NavLink to={`${base}/revisions`}>History</NavLink>
          </li>
          <li>
            <NavLink to={`${base}/schema`}>Schema</NavLink>
          </li>
          <li>
            <NavLink to={`${base}/assets`}>Media</NavLink>
          </li>
        </ul>
      </nav>

      <section className="panel status" aria-label="Project status" aria-live="polite">
        {projection.isSuccess && status ? (
          <>
            <span>
              Canon head <strong>r{status.head_revision}</strong>
            </span>
            {status.status === 'error' ? (
              <Badge tone="danger">Graph index needs operator attention</Badge>
            ) : status.current ? (
              <Badge tone="ok">Graph index current</Badge>
            ) : (
              <Badge tone="warn">
                Graph index {status.lag} revision{status.lag === 1 ? '' : 's'} behind (canon is
                unaffected)
              </Badge>
            )}
            {status.failed_deliveries > 0 && (
              <Badge tone="warn">Failed index deliveries: {status.failed_deliveries}</Badge>
            )}
          </>
        ) : projection.isError ? (
          <span className="error" role="alert">
            Project status is unavailable: {(projection.error as Error).message}
          </span>
        ) : (
          <span className="muted">Loading project status…</span>
        )}
        {access.data && (
          <Badge tone={access.data.can_write ? 'neutral' : 'warn'}>
            Your role: {access.data.role}
            {access.data.can_write ? '' : ' (read-only)'}
          </Badge>
        )}
      </section>

      {importState && importState.status !== 'completed' && (
        <p className={`notice ${importState.status === 'failed' ? 'danger' : 'warn'}`} role="alert">
          {IMPORT_TEXT[importState.status] ?? `Archive import status: ${importState.status}.`}{' '}
          <span className="muted">
            ({importState.commits_applied} of {importState.commits_total} revisions restored)
          </span>
        </p>
      )}

      <main id="main" tabIndex={-1}>
        <Outlet context={context} />
      </main>
    </div>
  )
}
