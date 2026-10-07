import { useCallback, useEffect, useState } from 'react'
import './App.css'

function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : ''
}

export default function App() {
  const [status, setStatus] = useState('loading')
  const [user, setUser] = useState(null)

  const load = useCallback(async () => {
    try {
      const response = await fetch('/auth/me', { credentials: 'include' })
      if (response.ok) {
        const data = await response.json()
        setUser(data.user)
        setStatus('authenticated')
      } else {
        setUser(null)
        setStatus('anonymous')
      }
    } catch {
      setUser(null)
      setStatus('anonymous')
    }
  }, [])

  // Session state lives on the server; it is read once on mount and all updates
  // happen after the network await rather than synchronously during render.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  async function signOut() {
    await fetch('/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': readCookie('novel_csrf') },
    })
    setStatus('loading')
    load()
  }

  return (
    <main className="shell">
      <header>
        <h1>Novel Engine</h1>
        <p className="tagline">Continuity-first fiction authoring</p>
      </header>

      {status === 'loading' && <p className="muted">Checking your session…</p>}

      {status === 'anonymous' && (
        <section className="panel">
          <p>You are not signed in.</p>
          <a className="button" href="/auth/login">
            Sign in
          </a>
        </section>
      )}

      {status === 'authenticated' && (
        <section className="panel">
          <p>
            Signed in as <strong>{user?.display_name || user?.email || user?.id}</strong>.
          </p>
          <button className="button" onClick={signOut} type="button">
            Sign out
          </button>
          <p className="muted">Workspaces and project authoring arrive in later slices.</p>
        </section>
      )}
    </main>
  )
}