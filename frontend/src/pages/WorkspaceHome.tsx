import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { getActiveWorkspaceId, setActiveWorkspaceId } from '../scope'

export function WorkspaceHome() {
  const navigate = useNavigate()

  const session = useQuery({ queryKey: ['session'], queryFn: api.session })
  const signedIn = session.isSuccess ? true : session.isError ? false : null

  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.listWorkspaces,
    enabled: signedIn === true && session.data?.auth_mode !== 'local',
  })

  const [workspaceId, setWorkspaceId] = useState<string | null>(getActiveWorkspaceId())
  const projects = useQuery({
    queryKey: ['projects', workspaceId],
    queryFn: () => api.listProjects(workspaceId as string),
    enabled: Boolean(workspaceId) && session.data?.auth_mode !== 'local',
  })

  // Local (self-hosted, single-user) mode has exactly one project and no workspace
  // list; the session reports its id.
  const local = session.data?.auth_mode === 'local'
  const localProjectId = session.data?.local?.project_id

  if (signedIn === null) return <main className="shell"><p className="muted" role="status">Checking your session…</p></main>
  if (signedIn === false) {
    return (
      <main className="shell">
        <header>
          <h1>Novel Engine</h1>
          <p className="tagline">Continuity-first fiction authoring</p>
        </header>
        <section className="panel">
          <p>You are not signed in.</p>
          <a className="button" href="/auth/login">Sign in</a>
        </section>
      </main>
    )
  }

  return (
    <main className="shell">
      <header>
        <h1>Novel Engine</h1>
        <p className="tagline">Select a workspace and project</p>
      </header>

      {local && localProjectId && (
        <section className="panel">
          <h2>Local project</h2>
          <p className="muted">This is a single-user installation with one project.</p>
          <Link
            className="button"
            to={`/projects/${localProjectId}`}
            onClick={() => setActiveWorkspaceId(null)}
          >
            Open project
          </Link>
        </section>
      )}

      {!local && (
      <section className="panel">
        <label htmlFor="workspace">Workspace</label>
        <select
          id="workspace"
          value={workspaceId ?? ''}
          onChange={(event) => {
            const value = event.target.value || null
            setWorkspaceId(value)
            setActiveWorkspaceId(value)
          }}
        >
          <option value="">Select a workspace…</option>
          {(workspaces.data ?? []).map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
      </section>
      )}

      {!local && workspaceId && (
        <section className="panel">
          <h2>Projects</h2>
          {projects.isLoading && <p className="muted">Loading projects…</p>}
          {projects.data && projects.data.length === 0 && <p className="muted">No projects yet.</p>}
          <ul className="list">
            {(projects.data ?? []).map((project) => (
              <li key={project.id}>
                <Link
                  to={`/projects/${project.id}`}
                  onClick={() => setActiveWorkspaceId(workspaceId)}
                >
                  {project.name}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!local && workspaces.data && workspaces.data.length > 0 && (
        <button
          className="button"
          onClick={() => navigate(`/projects/${projects.data?.[0]?.id ?? ''}`)}
          disabled={!projects.data?.length}
        >
          Open first project
        </button>
      )}
    </main>
  )
}
