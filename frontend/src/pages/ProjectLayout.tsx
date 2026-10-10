import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, isApiError, type CanonEntity, type Scope } from '../api/client'
import { CommandPalette } from '../components/CommandPalette'
import { GodHand } from '../components/GodHand'
import { Icon, type IconName } from '../components/icons'
import { NewEntryDialog } from '../components/NewEntryDialog'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button, EmptyState, Loading } from '../components/ui'
import { displayName, useSession, useSignOut } from '../lib/session'
import { initial, isOpenChange, roleLabel, typePlural } from '../lib/words'
import {
  ProjectProvider,
  scopedKey,
  useEntries,
  useInvalidateProject,
  type ProjectContext,
} from '../project'
import { getActiveWorkspaceId } from '../scope'

const IMPORT_TEXT: Record<string, string> = {
  importing: 'A backup is being restored into this project. What you see is incomplete until it finishes.',
  failed:
    'Restoring a backup into this project stopped part-way, so this project is incomplete. An owner should retry the restore or remove it.',
  removed: 'A failed restore was removed from this project.',
}

/** The entry types every new project starts with; nothing project-specific. */
const STARTER_SCHEMA = {
  label: 'Starter schema',
  description: '',
  entity_extensions: [],
  predicate_extensions: [],
}

function NavItem({
  to,
  icon,
  end,
  children,
  trailing,
}: {
  to: string
  icon: IconName
  end?: boolean
  children: string
  trailing?: React.ReactNode
}) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
      <Icon name={icon} />
      {children}
      {trailing}
    </NavLink>
  )
}

export function ProjectLayout() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const workspaceId = getActiveWorkspaceId()
  const scope: Scope = { workspaceId, projectId }
  const invalidate = useInvalidateProject(scope)
  const session = useSession()
  const { signOut, pending: signingOut } = useSignOut()

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
    queryKey: scopedKey(scope, 'mutations', 'open'),
    queryFn: () => api.listMutations(scope, { limit: 100 }),
    refetchInterval: 30_000,
  })
  const schemas = useQuery({
    queryKey: scopedKey(scope, 'schemas'),
    queryFn: () => api.listSchemaVersions(scope),
  })
  const entries = useEntries(scope)

  const canWrite = access.data?.can_write ?? false
  const canAdmin = access.data?.can_admin ?? false

  // A project cannot hold anything until its entry types are published once.
  // Do that quietly for the owner the first time the project is opened.
  const setup = useMutation({
    mutationFn: async () => {
      const draft = await api.createSchemaDraft(scope, STARTER_SCHEMA)
      return api.publishSchema(scope, draft.version.id)
    },
    onSettled: () => invalidate(),
  })
  const needsSetup = schemas.isSuccess && schemas.data.length === 0
  const startedSetup = useRef(false)
  useEffect(() => {
    if (needsSetup && canAdmin && !startedSetup.current) {
      startedSetup.current = true
      setup.mutate()
    }
  }, [needsSetup, canAdmin, setup])

  const [menuOpen, setMenuOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [newEntry, setNewEntry] = useState<{ type?: string; entity?: CanonEntity } | null>(null)
  const openNewEntry = useCallback((type?: string) => setNewEntry({ type }), [])
  const openFillIn = useCallback((entity: CanonEntity) => setNewEntry({ entity }), [])
  const [godHand, setGodHand] = useState<{ entity?: CanonEntity } | null>(null)
  const openGodHand = useCallback((entity?: CanonEntity) => setGodHand({ entity }), [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (isApiError(access.error) && [401, 403, 404].includes(access.error.status)) {
    const signedOut = access.error.status === 401
    return (
      <div className="center-page">
        <EmptyState
          icon="key"
          title={signedOut ? 'You’ve been signed out' : 'This project isn’t available to you'}
          action={
            signedOut ? (
              <a className="btn primary" href="/auth/login">
                Sign in again
              </a>
            ) : (
              <Link className="btn primary" to="/">
                Back to your library
              </Link>
            )
          }
        >
          {signedOut
            ? 'Your session ended. Sign in to pick up where you left off.'
            : 'It may have been removed, or you may not have been invited to it.'}
        </EmptyState>
      </div>
    )
  }

  const status = projection.data
  const context: ProjectContext = {
    scope,
    headRevision: status?.head_revision ?? 0,
    refreshHead: async () => {
      const fresh = await projection.refetch()
      if (fresh.data === undefined) throw fresh.error ?? new Error('Could not reload the latest version.')
      return fresh.data.head_revision
    },
    access: access.data,
    canWrite,
    canAdmin,
    userId: session.data?.user.id ?? null,
    newEntry: openNewEntry,
    fillIn: openFillIn,
    godHand: openGodHand,
  }
  const local = !workspaceId
  const projectName =
    projects.data?.find((item) => item.id === projectId)?.name ?? (local ? 'Your project' : 'Project')
  const base = `/projects/${projectId}`
  const awaiting = (pending.data?.items ?? []).filter((item) => isOpenChange(item.status)).length
  const importState = status?.import

  const active = entries.items.filter((item) => item.lifecycle === 'active')
  const counts = new Map<string, number>()
  for (const item of active) counts.set(item.type, (counts.get(item.type) ?? 0) + 1)
  const typesInUse = [...counts.keys()].sort(
    (a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0) || a.localeCompare(b),
  )

  const banners =
    (importState && importState.status !== 'completed') ||
    status?.status === 'error' ||
    needsSetup ||
    setup.isError

  return (
    <ProjectProvider value={context}>
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <button
        type="button"
        className={`scrim ${menuOpen ? 'open' : ''}`}
        aria-hidden="true"
        tabIndex={-1}
        onClick={() => setMenuOpen(false)}
      />

      <aside
        className={`sidebar ${menuOpen ? 'open' : ''}`}
        id="sidebar"
        // Following any link in the menu closes it on a small screen.
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('a')) setMenuOpen(false)
        }}
      >
        <Link className="brand" to="/" title="All projects">
          <Logo />
          <span className="wordmark">Novel Engine</span>
        </Link>
        {/* Shown only when the menu is a drawer: a visible way to put it away. */}
        <button type="button" className="btn ghost sm menu-close" onClick={() => setMenuOpen(false)}>
          <Icon name="x" size={15} />
          Close menu
        </button>

        <div className="project-switch">
          <span className="eyebrow">Project</span>
          {projects.data && projects.data.length > 1 ? (
            <select
              aria-label="Switch project"
              value={projectId}
              onChange={(event) => navigate(`/projects/${event.target.value}`)}
            >
              {projects.data.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="project-name">{projectName}</span>
          )}
          {/* A visible way back to the library; the logo is not the only door. */}
          <Link className="small" to="/">
            All projects
          </Link>
        </div>

        <nav aria-label="Project" className="stack">
          <div className="nav-group">
            <NavItem to={base} icon="home" end>
              Home
            </NavItem>
            <NavItem
              to={`${base}/bible`}
              icon="book"
              trailing={active.length > 0 ? <span className="count">{active.length}</span> : null}
            >
              Story bible
            </NavItem>
            {typesInUse.map((type) => (
              <Link
                key={type}
                to={`${base}/bible?type=${encodeURIComponent(type)}`}
                className={`nav-link sub ${
                  location.pathname === `${base}/bible` &&
                  new URLSearchParams(location.search).get('type') === type
                    ? 'active'
                    : ''
                }`}
              >
                {typePlural(type)}
                <span className="count">{counts.get(type)}</span>
              </Link>
            ))}
            <NavItem
              to={`${base}/review`}
              icon="inbox"
              trailing={
                awaiting > 0 ? (
                  <span className="pill">
                    {awaiting}
                    <span className="sr-only"> waiting</span>
                  </span>
                ) : null
              }
            >
              Review
            </NavItem>
            <NavItem to={`${base}/history`} icon="clock">
              History
            </NavItem>
          </div>

          <div className="nav-group">
            <span className="eyebrow nav-title">Set up</span>
            <NavItem to={`${base}/customize`} icon="sliders">
              Types and fields
            </NavItem>
            <NavItem to={`${base}/media`} icon="image">
              Media
            </NavItem>
            {access.data?.hosted && (
              <NavItem to={`${base}/assistant`} icon="spark">
                AI assistant
              </NavItem>
            )}
            {access.data?.hosted && (
              <NavItem to={`${base}/people`} icon="user">
                People
              </NavItem>
            )}
          </div>
        </nav>

        <div className="sidebar-foot">
          <div className="save-state" aria-live="polite">
            {projection.isError ? (
              <>
                <span className="dot danger" aria-hidden="true" />
                Status unavailable
              </>
            ) : !status ? (
              <span className="muted">Loading…</span>
            ) : (
              <>
                <span className={`dot ${status.status === 'error' ? 'warn' : ''}`} aria-hidden="true" />
                <span>Version {status.head_revision} · saved</span>
              </>
            )}
          </div>
          {access.data && !canWrite && <span className="badge warn">Reader: you can look, not change</span>}
          <div className="user-row">
            <span className="avatar sm round tint-violet" aria-hidden="true">
              {initial(displayName(session.data))}
            </span>
            <span className="who small">
              <strong>{displayName(session.data)}</strong>
              {access.data && <span className="muted">{roleLabel(access.data.role)}</span>}
            </span>
          </div>
          <div className="actions" style={{ justifyContent: 'space-between', padding: '0 0.3rem' }}>
            <ThemeToggle />
            <button
              type="button"
              className="btn ghost sm"
              disabled={signingOut}
              onClick={() => void signOut()}
            >
              <Icon name="logout" size={15} />
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="btn ghost sm menu-btn"
            aria-expanded={menuOpen}
            aria-controls="sidebar"
            onClick={() => setMenuOpen(true)}
          >
            <Icon name="menu" size={15} />
            Menu
          </button>
          <button type="button" className="search-trigger" onClick={() => setPaletteOpen(true)}>
            <Icon name="search" />
            <span>Search your story bible…</span>
            <kbd>Ctrl K</kbd>
          </button>
          <span className="spacer" />
          {canWrite && (
            <Button icon="hand" onClick={() => openGodHand()}>
              Change anything
            </Button>
          )}
          {canWrite && (
            <Button variant="primary" icon="plus" onClick={() => openNewEntry()}>
              New entry
            </Button>
          )}
        </header>

        <main id="main" className="content" tabIndex={-1}>
          {banners && (
          <div className="stack" style={{ marginBottom: '1.25rem' }}>
            {importState && importState.status !== 'completed' && (
              <p className={`notice ${importState.status === 'failed' ? 'danger' : 'warn'}`} role="alert">
                <span>
                  {IMPORT_TEXT[importState.status] ?? `Restore status: ${importState.status}.`}{' '}
                  <span className="muted">
                    ({importState.commits_applied} of {importState.commits_total} versions restored)
                  </span>
                </span>
              </p>
            )}
            {status?.status === 'error' && (
              <p className="notice warn" role="alert">
                <span>
                  <strong>Search is out of date.</strong> Your story bible itself is safe and
                  complete; the person who runs this site needs to rebuild the search index.
                </span>
              </p>
            )}
            {needsSetup && setup.isPending && <Loading>Getting your story bible ready…</Loading>}
            {needsSetup && !canAdmin && access.isSuccess && (
              <p className="notice plain" role="note">
                This project is still being set up by its owner. You can look around, but nothing
                can be added yet.
              </p>
            )}
            {setup.isError && (
              <p className="notice danger" role="alert">
                <span>
                  <strong>The project could not be set up automatically.</strong>{' '}
                  <Link to={`${base}/customize`}>Finish setting up by hand</Link>.
                </span>
              </p>
            )}
          </div>
          )}
          <Outlet context={context} />
        </main>
      </div>

      {paletteOpen && (
        <CommandPalette
          scope={scope}
          canWrite={canWrite}
          onClose={() => setPaletteOpen(false)}
          onNewEntry={openNewEntry}
          onGodHand={openGodHand}
        />
      )}
    </div>
      {godHand && (
        <GodHand
          key={godHand.entity?.id ?? 'any'}
          entity={godHand.entity}
          onClose={() => setGodHand(null)}
        />
      )}
      {newEntry && (
        <NewEntryDialog
          // A fresh dialog for each thing, so nothing typed for one leaks into the next.
          key={newEntry.entity?.id ?? 'new'}
          type={newEntry.type}
          entity={newEntry.entity}
          onClose={() => setNewEntry(null)}
        />
      )}
    </ProjectProvider>
  )
}