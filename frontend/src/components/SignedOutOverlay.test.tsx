import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { reauth, resetReauth } from '../lib/reauth'
import { SignedOutOverlay } from './SignedOutOverlay'

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  reauth.pollMs = 20
})
afterEach(() => {
  resetReauth()
  vi.unstubAllGlobals()
  document.cookie = 'novel_csrf=; Max-Age=0'
})

/** A server that is signed in, signs the browser out, and lets it back in on request. */
function server() {
  const state = { signedIn: true, saves: [] as { token: string | null; body: unknown }[] }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (!state.signedIn) return json(401, { code: 'unauthorized', detail: 'A valid session is required.' })
      if (path === '/auth/me') return json(200, { user: { id: 'usr_1' }, auth_mode: 'hosted' })
      state.saves.push({
        token: new Headers(init?.headers).get('X-CSRF-Token'),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      })
      return json(200, { id: 'ws_1', slug: 'mine', name: 'Mine' })
    }),
  )
  return state
}

describe('being signed out in the middle of work', () => {
  it('holds the save, keeps the page, and finishes the save once signed in again', async () => {
    const state = server()
    render(<SignedOutOverlay />)
    await api.session()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()

    // The session ends while the author is working, and they save.
    state.signedIn = false
    document.cookie = 'novel_csrf=old-token'
    let settled: 'saved' | 'failed' | null = null
    const saving = api.createWorkspace({ name: 'Mine', slug: 'mine' }).then(
      () => (settled = 'saved'),
      () => (settled = 'failed'),
    )

    const dialog = await screen.findByRole('alertdialog', { name: 'You’ve been signed out' })
    expect(dialog).toHaveTextContent('Nothing you typed has been lost.')
    const link = screen.getByRole('link', { name: 'Sign in again (opens in a new tab)' })
    // In a new tab, so this page and everything typed into it stay where they are.
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('href', '/auth/login')
    await new Promise((resolve) => setTimeout(resolve, 80))
    // The save has neither failed nor been dropped: it is waiting.
    expect(settled).toBeNull()
    expect(state.saves).toHaveLength(0)

    // They sign in in the other tab, which also issues a new write token.
    state.signedIn = true
    document.cookie = 'novel_csrf=new-token'
    await saving

    expect(settled).toBe('saved')
    expect(state.saves).toEqual([{ token: 'new-token', body: { name: 'Mine', slug: 'mine' } }])
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('says so when "I’ve signed in" is pressed too early, and keeps waiting', async () => {
    const state = server()
    const user = userEvent.setup()
    render(<SignedOutOverlay />)
    await api.session()
    state.signedIn = false
    reauth.pollMs = 60_000
    const reading = api.listWorkspaces()

    await user.click(await screen.findByRole('button', { name: 'I’ve signed in' }))
    expect(await screen.findByText(/Not signed in yet/)).toBeInTheDocument()
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()

    state.signedIn = true
    await user.click(screen.getByRole('button', { name: 'I’ve signed in' }))
    await reading
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('does not hold a visitor who was never signed in: they get the ordinary sign-in screens', async () => {
    const state = server()
    state.signedIn = false
    render(<SignedOutOverlay />)

    await expect(api.session()).rejects.toMatchObject({ status: 401 })
    await expect(api.listWorkspaces()).rejects.toMatchObject({ status: 401 })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('answers "am I signed in?" plainly even after signing out, so the front page can show', async () => {
    const state = server()
    render(<SignedOutOverlay />)
    await api.session()
    state.signedIn = false

    await expect(api.session()).rejects.toMatchObject({ status: 401 })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })
})
