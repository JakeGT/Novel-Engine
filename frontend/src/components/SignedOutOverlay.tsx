import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { checkSignedIn, isWaitingForSignIn, onSignInWait } from '../lib/reauth'
import { Button } from './ui'

/**
 * Shown over everything when the session ends mid-work. The page underneath is
 * left exactly as it was, so nothing typed is lost; signing in happens in another
 * tab and whatever was being saved is then sent again by itself.
 */
export function SignedOutOverlay() {
  const waiting = useSyncExternalStore(onSignInWait, isWaitingForSignIn)
  const [checking, setChecking] = useState(false)
  const [notYet, setNotYet] = useState(false)
  const link = useRef<HTMLAnchorElement>(null)
  useEffect(() => {
    if (waiting) link.current?.focus()
  }, [waiting])
  if (!waiting) return null

  return (
    <div className="backdrop signed-out">
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="signed-out-title"
        aria-describedby="signed-out-text"
      >
        <div className="dialog-body stack">
          <h2 id="signed-out-title">You’ve been signed out</h2>
          <p id="signed-out-text">
            <strong>Nothing you typed has been lost.</strong> This page is being kept exactly as
            it is. Sign in again in a new tab, then come back here: whatever was being saved
            will be saved, and you can carry on.
          </p>
          <div className="actions">
            <a ref={link} className="btn primary" href="/auth/login" target="_blank" rel="noreferrer">
              Sign in again (opens in a new tab)
            </a>
            <Button
              disabled={checking}
              onClick={() => {
                setChecking(true)
                setNotYet(false)
                void checkSignedIn().then((signedIn) => {
                  setChecking(false)
                  setNotYet(!signedIn)
                })
              }}
            >
              {checking ? 'Checking…' : 'I’ve signed in'}
            </Button>
          </div>
          <p className="hint" role="status">
            {notYet
              ? 'Not signed in yet. Finish signing in in the other tab, then try again.'
              : 'Waiting for you to sign in. This page checks by itself every few seconds.'}
          </p>
        </div>
      </div>
    </div>
  )
}
