import { useEffect } from 'react'
import { shouldHoldFor401 } from './reauth'

const EVERY_MS = 4 * 60_000

/**
 * While the site is open, the session is kept alive: a small request every few
 * minutes, and one whenever the tab is returned to. A session ends from disuse,
 * and an open page with unsaved writing in it is not disuse.
 */
export function KeepSignedIn() {
  useEffect(() => {
    const touch = () => {
      // Only once signed in, and only when the tab is in use or being returned to.
      if (!shouldHoldFor401() || document.visibilityState !== 'visible') return
      void fetch('/auth/me', { credentials: 'include', headers: { Accept: 'application/json' } }).catch(
        () => undefined,
      )
    }
    const timer = window.setInterval(touch, EVERY_MS)
    document.addEventListener('visibilitychange', touch)
    window.addEventListener('focus', touch)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', touch)
      window.removeEventListener('focus', touch)
    }
  }, [])
  return null
}
