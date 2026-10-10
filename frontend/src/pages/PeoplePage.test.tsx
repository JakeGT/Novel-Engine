import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, OWNER, problem, renderInProject, reply, VIEWER } from '../test/harness'
import { InvitePage } from './InvitePage'
import { PeoplePage } from './PeoplePage'

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

const jake = { workspace_id: 'ws_1', user_id: 'usr_1', role: 'owner', display_name: 'Jake Talling', email: 'jake@example.com' }
const sam = { workspace_id: 'ws_1', user_id: 'usr_2', role: 'editor', display_name: 'Sam Reed', email: 'sam@example.com' }
const future = new Date(Date.now() + 86_400_000 * 5).toISOString()
const invitation = {
  id: 'inv_1',
  workspace_id: 'ws_1',
  role: 'viewer',
  email: 'ana@example.com',
  created_at: new Date().toISOString(),
  expires_at: future,
  accepted_at: null,
  revoked_at: null,
}

describe('PeoplePage', () => {
  it('lists members by name and lets an owner change roles, but never strand the workspace', async () => {
    const api = mockApi({
      'GET /members': [jake, sam],
      'GET /invitations': [],
      'PATCH /members/usr_2': { ...sam, role: 'viewer' },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PeoplePage />, { context: { userId: 'usr_1', access: OWNER } })

    const members = await screen.findByRole('region', { name: 'Members' })
    const rows = await within(members).findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Jake Talling (you)')
    // The only owner cannot be demoted or removed.
    expect(within(rows[0]).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(rows[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(container).not.toHaveTextContent('usr_')
    await expectAccessible(container)

    await user.selectOptions(within(rows[1]).getByLabelText('Role of Sam Reed'), 'Reader')
    await waitFor(() => expect(api.find('PATCH', '/members/usr_2')).toHaveLength(1))
    expect(api.find('PATCH', '/members/usr_2')[0].body).toEqual({ role: 'viewer' })
  })

  it('creates an invitation link that is shown once, and cancels a waiting one', async () => {
    const api = mockApi({
      'GET /members': [jake],
      'GET /invitations': [invitation],
      'POST /invitations': reply(201, { invitation, token: 'tok-abc_123' }),
      'POST /invitations/inv_1/revoke': { revoked: true },
    })
    const user = userEvent.setup()
    renderInProject(<PeoplePage />, { context: { userId: 'usr_1', access: OWNER } })

    await user.type(await screen.findByLabelText('Their email (optional)'), 'ana@example.com')
    await user.selectOptions(screen.getByLabelText('They will be'), 'Reader')
    await user.click(screen.getByRole('button', { name: 'Create invitation link' }))

    expect(await screen.findByLabelText('Invitation link')).toHaveValue(
      `${window.location.origin}/invite/tok-abc_123`,
    )
    expect(screen.getByText(/shown only now and cannot be shown again/)).toBeInTheDocument()
    expect(api.find('POST', '/invitations')[0].body).toEqual({ role: 'viewer', email: 'ana@example.com' })

    await user.click(screen.getByRole('button', { name: 'Cancel invitation for ana@example.com' }))
    await waitFor(() => expect(api.find('POST', '/revoke')).toHaveLength(1))
  })

  it('shows a member who is not an owner the list, and nothing they cannot use', async () => {
    const api = mockApi({ 'GET /members': [{ ...jake, email: null }, { ...sam, email: null }] })
    renderInProject(<PeoplePage />, { context: { userId: 'usr_2', access: VIEWER } })

    expect(await screen.findByText('Jake Talling')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('Only an owner can invite people')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    // The invitation list is an owner's business and is not even requested.
    expect(api.find('GET', '/invitations')).toHaveLength(0)
  })
})

function renderInvite() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/invite/tok-abc_123']}>
        <Routes>
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route path="/" element={<p>library</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('InvitePage', () => {
  it('accepts the invitation for a signed-in person and opens their library', async () => {
    const api = mockApi({
      'GET /auth/me': { user: { id: 'usr_3', email: 'ana@example.com', display_name: 'Ana' }, auth_mode: 'hosted' },
      'POST /invitations/accept': { ...sam, user_id: 'usr_3', workspace_id: 'ws_9' },
    })
    renderInvite()

    expect(await screen.findByText('library')).toBeInTheDocument()
    expect(api.find('POST', '/invitations/accept')[0].body).toEqual({ token: 'tok-abc_123' })
    expect(sessionStorage.getItem('novel_active_workspace')).toBe('ws_9')
  })

  it('remembers the link and asks a signed-out person to sign in first', async () => {
    const api = mockApi({ 'GET /auth/me': problem(401, 'unauthenticated', 'Sign in.') })
    renderInvite()

    expect(await screen.findByRole('link', { name: 'Sign in to accept' })).toHaveAttribute('href', '/auth/login')
    expect(sessionStorage.getItem('novel_pending_invite')).toBe('tok-abc_123')
    expect(api.find('POST', '/invitations/accept')).toHaveLength(0)
  })

  it('explains an invitation meant for someone else, or one that is no longer valid', async () => {
    mockApi({
      'GET /auth/me': { user: { id: 'usr_3', email: 'x@example.com', display_name: 'X' }, auth_mode: 'hosted' },
      'POST /invitations/accept': problem(403, 'forbidden', 'This invitation was issued to a different account.'),
    })
    const first = renderInvite()
    expect(await screen.findByRole('alert')).toHaveTextContent('made for a different account')
    first.unmount()

    mockApi({
      'GET /auth/me': { user: { id: 'usr_3', email: 'x@example.com', display_name: 'X' }, auth_mode: 'hosted' },
      'POST /invitations/accept': problem(409, 'conflict', 'Not acceptable.'),
    })
    renderInvite()
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer valid')
  })
})
