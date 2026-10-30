import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api, isApiError, type Project } from '../api/client'
import { Icon } from '../components/icons'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button, Dialog, ErrorNotice, Field, Loading } from '../components/ui'
import { slugify, uniqueSlug } from '../lib/identifiers'
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

/**
 * A newly admitted person's first screen. Getting in was decided at sign-in, so
 * there is nothing to wait for: they name a place of their own and a first story,
 * and land in it ready to describe that story.
 */
function Onboarding({ name }: { name: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [workspace, setWorkspace] = useState(name ? `${name}’s library` : 'My library')
  const [story, setStory] = useState('')
  const [problems, setProblems] = useState<{ workspace?: string; story?: string }>({})

  const create = useMutation({
    mutationFn: async () => {
      const made = await api.createWorkspace({ name: workspace.trim(), slug: uniqueSlug(workspace) })
      setActiveWorkspaceId(made.id)
      const project = await api.createProject(made.id, { name: story.trim(), slug: slugify(story) })
      return project
    },
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['workspaces'] })
      navigate(`/projects/${project.id}`)
    },
    // A workspace may have been made before the project failed: show it rather than ask again.
    onError: () => queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const found: { workspace?: string; story?: string } = {}
    if (!workspace.trim()) found.workspace = 'Give your library a name.'
    if (slugify(story).length < 2) found.story = 'Give your story a working title of at least two letters.'
    setProblems(found)
    if (Object.keys(found).length === 0) create.mutate()
  }
  const refused = isApiError(create.error) && create.error.status === 403

  return (
    <section className="onboarding" aria-labelledby="onboarding-heading">
      <p className="eyebrow">Welcome</p>
      <h1 id="onboarding-heading">{name ? `Let’s set up your library, ${name}.` : 'Let’s set up your library.'}</h1>
      <p className="lede">
        Your library is a private place for your stories. Each story gets its own story bible.
        You can invite other people in later.
      </p>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field label="What is your first story called?" hint="A working title is fine; you can change it." error={problems.story}>
          {(props) => (
            <input
              {...props}
              data-autofocus
              autoFocus
              value={story}
              placeholder="The Salt Road"
              onChange={(event) => setStory(event.target.value)}
            />
          )}
        </Field>
        <Field label="Name your library" error={problems.workspace}>
          {(props) => (
            <input {...props} value={workspace} onChange={(event) => setWorkspace(event.target.value)} />
          )}
        </Field>
        {refused ? (
          <p className="notice warn" role="alert">
            New libraries cannot be created on this site right now. Ask the person who invited
            you to add you to theirs, then reload this page.
          </p>
        ) : (
          <ErrorNotice error={create.error} prefix="Your library was not created" />
        )}
        <div className="actions">
          <Button variant="primary" size="lg" type="submit" disabled={create.isPending}>
            {create.isPending ? 'Setting up…' : 'Create my library'}
          </Button>
        </div>
      </form>
      <ol className="steps" aria-label="What happens next">
        <li className="step done">
          <strong>Sign in</strong>
          <span className="muted">You’re in.</span>
        </li>
        <li className="step">
          <strong>Name your story</strong>
          <span className="muted">That’s this page.</span>
        </li>
        <li className="step">
          <strong>Describe it</strong>
          <span className="muted">Tell the assistant about it in your own words, and review what it drafts.</span>
        </li>
      </ol>
    </section>
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
  // Nothing to come back to yet: this is the first visit, and onboarding takes over.
  const firstVisit = !local && workspaces.isSuccess && list.length === 0
  const location = useLocation()
  const joined = Boolean((location.state as { joined?: boolean } | null)?.joined)
  // A fresh sign-in (before changing a site key) that the provider answered without
  // asking for the password again: nothing was changed.
  const staleSignIn = new URLSearchParams(location.search).get('signin') === 'stale'

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
          {session.data?.platform_admin && (
            <Link className="btn ghost sm" to="/site">
              Site settings
            </Link>
          )}
          <Button variant="ghost" size="sm" icon="logout" onClick={() => void signOut()} disabled={signingOut}>
            Sign out
          </Button>
        </div>
      </header>

      <main id="main" className="section-gap">
        {/* Shown only once it is known there is something to come back to. */}
        {(local || (workspaces.isSuccess && !firstVisit)) && (
          <div className="page-head">
            <div>
              <h1>{name ? `Welcome back, ${name}.` : 'Welcome back.'}</h1>
              <p className="lede">Pick a project to open its story bible.</p>
            </div>
          </div>
        )}

        {staleSignIn && (
          <div className="notice warn" role="alert">
            <span>
              Your sign-in provider let you back in without asking for your password, so the site’s
              settings were not opened for changes. Try again, and enter your password when asked.
            </span>
            <a className="btn primary" href="/auth/login?fresh=1&return_to=%2Fsite">
              Sign in again
            </a>
          </div>
        )}

        {joined && (
          <p className="notice ok" role="status">
            You’ve joined the workspace. Its projects are below.
          </p>
        )}

        {local && localProjectId && (
          <section aria-labelledby="local-projects-heading" className="stack">
            <h2 id="local-projects-heading" className="eyebrow">
              This computer · project
            </h2>
            <div className="project-grid">
              <Link
                className="project-card"
                to={`/projects/${localProjectId}`}
                onClick={() => setActiveWorkspaceId(null)}
              >
                <span className="eyebrow">Project</span>
                <h3>Your project</h3>
                <span className="muted small">Open project →</span>
              </Link>
            </div>
          </section>
        )}

        {!local && workspaces.isPending && <Loading>Finding your workspaces…</Loading>}
        {!local && <ErrorNotice error={workspaces.error} prefix="Could not load your workspaces" />}

        {!local && workspaces.isSuccess && list.length === 0 && <Onboarding name={name} />}

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
