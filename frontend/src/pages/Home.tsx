import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api, type Project } from '../api/client'
import { Icon } from '../components/icons'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button, Dialog, EmptyState, ErrorNotice, Field, Loading } from '../components/ui'
import { slugify } from '../lib/identifiers'
import { pendingInvite } from '../lib/invite'
import { displayName, firstName, useSession, useSignOut } from '../lib/session'
import { initial } from '../lib/words'
import { getActiveWorkspaceId, setActiveWorkspaceId } from '../scope'
import { Landing } from './Landing'

/** Signed out: the public front page. Signed in: the author's library. */
export function Home() {
  const session = useSession()
  if (session.isPending) {
    return (
      <div className="center-page">
        <Loading>Opening Novel Engine…</Loading>
      </div>
    )
  }
  if (session.isError) return <Landing />
  // Signed in after opening an invitation link while signed out: go back to it.
  const invite = pendingInvite()
  if (invite) return <Navigate to={`/invite/${encodeURIComponent(invite)}`} replace />
  return <Library />
}

function NewProjectDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => api.createProject(workspaceId, { name: name.trim(), slug: slugify(name) }),
    onSuccess: async (project: Project) => {
      await queryClient.invalidateQueries({ queryKey: ['projects', workspaceId] })
      setActiveWorkspaceId(workspaceId)
      navigate(`/projects/${project.id}`)
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (slugify(name).length < 2) {
      return setProblem('Give the project a name with at least two letters or numbers.')
    }
    setProblem(null)
    create.mutate()
  }

  return (
    <Dialog title="Start a new project" onClose={onClose}>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field
          label="What is it called?"
          hint="A working title is fine. Each project has its own separate story bible."
          error={problem}
        >
          {(props) => (
            <input
              {...props}
              data-autofocus
              value={name}
              placeholder="The Salt Road"
              onChange={(event) => setName(event.target.value)}
            />
          )}
        </Field>
        <ErrorNotice error={create.error} prefix="The project was not created" />
        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create project'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function Library() {
  const session = useSession()
  const { signOut, pending: signingOut } = useSignOut()
  const local = session.data?.auth_mode === 'local'
  const localProjectId = session.data?.local?.project_id

  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.listWorkspaces,
    enabled: !local,
  })
  const [chosen, setChosen] = useState<string | null>(getActiveWorkspaceId())
  const list = workspaces.data ?? []
  // Most people have exactly one workspace: open it without asking.
  const workspaceId = list.find((item) => item.id === chosen)?.id ?? list[0]?.id ?? null
  const projects = useQuery({
    queryKey: ['projects', workspaceId],
    queryFn: () => api.listProjects(workspaceId as string),
    enabled: !local && Boolean(workspaceId),
  })
  const [creating, setCreating] = useState(false)
  const name = firstName(session.data)
  const joined = Boolean((useLocation().state as { joined?: boolean } | null)?.joined)

  return (
    <div className="library" id="top">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="library-top">
        <a className="brand" href="#top" aria-label="Novel Engine, back to the top">
          <Logo />
          <span className="wordmark">Novel Engine</span>
        </a>
        <div className="actions">
          <ThemeToggle />
          <span className="avatar sm round tint-violet" aria-hidden="true">
            {initial(displayName(session.data))}
          </span>
          <span className="small">{displayName(session.data)}</span>
          <Button variant="ghost" size="sm" icon="logout" onClick={() => void signOut()} disabled={signingOut}>
            Sign out
          </Button>
        </div>
      </header>

      <main id="main" className="section-gap">
        <div className="page-head">
          <div>
            <h1>{name ? `Welcome back, ${name}.` : 'Welcome back.'}</h1>
            <p className="lede">Pick a project to open its story bible.</p>
          </div>
        </div>

        {joined && (
          <p className="notice ok" role="status">
            You’ve joined the workspace. Its projects are below.
          </p>
        )}

        {local && localProjectId && (
          <div className="project-grid">
            <Link
              className="project-card"
              to={`/projects/${localProjectId}`}
              onClick={() => setActiveWorkspaceId(null)}
            >
              <span className="eyebrow">This computer</span>
              <h3>Your project</h3>
              <span className="muted small">Open project →</span>
            </Link>
          </div>
        )}

        {!local && workspaces.isPending && <Loading>Finding your workspaces…</Loading>}
        {!local && <ErrorNotice error={workspaces.error} prefix="Could not load your workspaces" />}

        {!local && workspaces.isSuccess && list.length === 0 && (
          <EmptyState icon="key" title="You’re signed in, but not in a workspace yet">
            Novel Engine is invitation-only. Ask the person who invited you to add you to their
            workspace, then reload this page.
          </EmptyState>
        )}

        {!local && list.length > 1 && (
          <div className="chips" role="group" aria-label="Workspace">
            {list.map((workspace) => (
              <button
                key={workspace.id}
                type="button"
                className="chip"
                aria-pressed={workspace.id === workspaceId}
                onClick={() => {
                  setChosen(workspace.id)
                  setActiveWorkspaceId(workspace.id)
                }}
              >
                {workspace.name}
              </button>
            ))}
          </div>
        )}

        {!local && workspaceId && (
          <section aria-labelledby="projects-heading" className="stack">
            <h2 id="projects-heading" className="eyebrow">
              {list.find((item) => item.id === workspaceId)?.name ?? 'Workspace'} · projects
            </h2>
            {projects.isPending && <Loading>Loading projects…</Loading>}
            <ErrorNotice error={projects.error} prefix="Could not load projects" />
            {projects.isSuccess && (
              <div className="project-grid">
                {projects.data.map((project) => (
                  <Link
                    key={project.id}
                    className="project-card"
                    to={`/projects/${project.id}`}
                    onClick={() => setActiveWorkspaceId(workspaceId)}
                  >
                    <span className="eyebrow">Project</span>
                    <h3>{project.name}</h3>
                    <span className="muted small">Open story bible →</span>
                  </Link>
                ))}
                <button type="button" className="project-card new" onClick={() => setCreating(true)}>
                  <Icon name="plus" size={22} />
                  New project
                </button>
              </div>
            )}
          </section>
        )}
      </main>

      {creating && workspaceId && (
        <NewProjectDialog workspaceId={workspaceId} onClose={() => setCreating(false)} />
      )}
    </div>
  )
}
