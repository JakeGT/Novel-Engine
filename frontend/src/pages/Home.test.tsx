import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, problem, reply } from '../test/harness'
import { Home } from './Home'

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

function renderHome() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/projects/:projectId" element={<p>project page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const session = { user: { id: 'usr_1', email: 'jake@example.com', display_name: 'Jake Talling' }, auth_mode: 'hosted' }
const workspace = { id: 'ws_1', slug: 'studio', name: 'Jake’s Studio' }
const project = { id: 'prj_1', workspace_id: 'ws_1', slug: 'first-novel', name: 'First Novel' }

describe('Home', () => {
  it('shows the public front page to a signed-out visitor', async () => {
    mockApi({ 'GET /auth/me': problem(401, 'unauthenticated', 'Sign in.') })
    const { container } = renderHome()

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('needs a memory.')
    expect(screen.getAllByRole('link', { name: /Sign in|Open your library/ })[0]).toHaveAttribute(
      'href',
      '/auth/login',
    )
    await expectAccessible(container)
  })

  it('opens the only workspace straight away and lists its projects', async () => {
    mockApi({
      'GET /auth/me': session,
      'GET /workspaces': [workspace],
      'GET /workspaces/ws_1/projects': [project],
    })
    const user = userEvent.setup()
    const { container } = renderHome()

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Welcome back, Jake.')
    const card = await screen.findByRole('link', { name: /First Novel/ })
    await expectAccessible(container)
    await user.click(card)
    expect(await screen.findByText('project page')).toBeInTheDocument()
    // Project pages scope every request to the workspace that was open.
    expect(sessionStorage.getItem('novel_active_workspace')).toBe('ws_1')
  })

  it('creates a project from just a name', async () => {
    const api = mockApi({
      'GET /auth/me': session,
      'GET /workspaces': [workspace],
      'GET /workspaces/ws_1/projects': [project],
      'POST /workspaces/ws_1/projects': reply(201, { ...project, id: 'prj_2', name: 'The Salt Road' }),
    })
    const user = userEvent.setup()
    renderHome()

    await user.click(await screen.findByRole('button', { name: 'New project' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new project' })
    await user.click(within(dialog).getByRole('button', { name: 'Create project' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('at least two letters')

    await user.type(within(dialog).getByLabelText('What is it called?'), 'The Salt Road')
    await user.click(within(dialog).getByRole('button', { name: 'Create project' }))

    expect(await screen.findByText('project page')).toBeInTheDocument()
    expect(api.find('POST', '/projects')[0].body).toEqual({ name: 'The Salt Road', slug: 'the-salt-road' })
  })

  it('explains an account with no workspace instead of showing an empty picker', async () => {
    mockApi({ 'GET /auth/me': session, 'GET /workspaces': [] })
    renderHome()
    expect(await screen.findByText('You’re signed in, but not in a workspace yet')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New project' })).not.toBeInTheDocument()
  })

  it('offers the single project of a self-hosted copy', async () => {
    mockApi({
      'GET /auth/me': {
        user: { id: 'local', email: null, display_name: 'Local Author' },
        auth_mode: 'local',
        local: { workspace_id: 'local-workspace', project_id: 'local-project' },
      },
    })
    renderHome()
    expect(await screen.findByRole('link', { name: /Your project/ })).toHaveAttribute(
      'href',
      '/projects/local-project',
    )
  })

  it('signs out here and at the identity provider', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    const api = mockApi({
      'GET /auth/me': session,
      'GET /workspaces': [workspace],
      'GET /workspaces/ws_1/projects': [project],
      'POST /auth/logout': { status: 'logged_out', logout_url: 'https://auth.example/end' },
    })
    const user = userEvent.setup()
    renderHome()

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://auth.example/end'))
    expect(api.find('POST', '/auth/logout')).toHaveLength(1)
  })
})
