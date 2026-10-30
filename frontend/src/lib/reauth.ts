// Being signed out must never cost work. When a request is refused because the
// session has ended, the request waits here instead of failing: the page stays
// exactly as it is, the author signs in again in another tab, and the request is
// then sent again. Nothing typed is lost and nothing has to be redone.

type Listener = (waiting: boolean) => void

const listeners = new Set<Listener>()
let waiting: Promise<void> | null = null
let release: (() => void) | null = null
// A page that has never been signed in has nothing to protect: it is shown the
// ordinary "sign in" screens instead of being held.
let everSignedIn = false

/** How often to look for a new session while waiting. Shortened in tests. */
export const reauth = { pollMs: 3000 }

export function noteSignedIn(): void {
  everSignedIn = true
}

export function shouldHoldFor401(): boolean {
  return everSignedIn
}

export function isWaitingForSignIn(): boolean {
  return waiting !== null
}

export function onSignInWait(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

async function signedInNow(): Promise<boolean> {
  try {
    const response = await fetch('/auth/me', { credentials: 'include', headers: { Accept: 'application/json' } })
    return response.ok
  } catch {
    // Offline is not signed in, but it is not a reason to give up either.
    return false
  }
}

/** Look now, rather than at the next interval (the author says they have signed in). */
export async function checkSignedIn(): Promise<boolean> {
  if (!(await signedInNow())) return false
  release?.()
  return true
}

/** Resolves once there is a session again. Every refused request shares one wait. */
export function waitForSignIn(): Promise<void> {
  if (waiting) return waiting
  waiting = new Promise<void>((resolve) => {
    const timer = window.setInterval(() => void checkSignedIn(), reauth.pollMs)
    const onReturn = () => {
      if (document.visibilityState === 'visible') void checkSignedIn()
    }
    window.addEventListener('focus', onReturn)
    document.addEventListener('visibilitychange', onReturn)
    release = () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onReturn)
      document.removeEventListener('visibilitychange', onReturn)
      waiting = null
      release = null
      listeners.forEach((listener) => listener(false))
      resolve()
    }
  })
  listeners.forEach((listener) => listener(true))
  return waiting
}

/** For tests: forget everything between cases. */
export function resetReauth(): void {
  release?.()
  everSignedIn = false
}
