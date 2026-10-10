import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, isApiError } from '../api/client'
import { Logo } from '../components/Logo'
import { ErrorNotice, Loading } from '../components/ui'
import { clearPendingInvite, rememberInvite } from '../lib/invite'
import { useSession } from '../lib/session'
import { setActiveWorkspaceId } from '../scope'

/**
 * Where an invitation link lands. Signed in: the invitation is accepted and the
 * person goes to their library. Signed out: the link is remembered for this
 * browser tab, and they are sent to sign in first.
 */
export function InvitePage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const started = useRef(false)

  const accept = useMutation({
    mutationFn: () => api.acceptInvitation(token),
    onSuccess: (membership) => {
      clearPendingInvite()
      setActiveWorkspaceId(membership.workspace_id)
      navigate('/', { replace: true, state: { joined: true } })
    },
    onError: () => clearPendingInvite(),
  })
  const { mutate } = accept

  useEffect(() => {
    if (session.isSuccess && !started.current) {
      started.current = true
      mutate()
    }
    if (session.isError) rememberInvite(token)
  }, [session.isSuccess, session.isError, mutate, token])

  return (
    <div className="center-page">
      <main className="stack" style={{ maxWidth: '30rem', justifyItems: 'start' }}>
        <span className="brand">
          <Logo />
          <span className="wordmark">Novel Engine</span>
        </span>
        <h1>You’ve been invited.</h1>

        {(session.isPending || accept.isPending) && <Loading>Opening your invitation…</Loading>}

        {session.isError && (
          <>
            <p className="lede">
              Sign in to join. If you were invited by email, use the account with that address.
            </p>
            <a className="btn primary lg" href="/auth/login">
              Sign in to accept
            </a>
          </>
        )}

        {accept.isError && (
          <>
            {isApiError(accept.error) && accept.error.status === 403 ? (
              <p className="notice warn" role="alert">
                This invitation was made for a different account. Sign out, then sign in with the
                address it was sent to.
              </p>
            ) : isApiError(accept.error) && [404, 409, 410].includes(accept.error.status) ? (
              <p className="notice warn" role="alert">
                This invitation is no longer valid. It may have been used, cancelled, or expired.
                Ask the person who invited you for a new link.
              </p>
            ) : (
              <ErrorNotice error={accept.error} prefix="The invitation could not be accepted" />
            )}
            <Link className="btn" to="/">
              Go to your library
            </Link>
          </>
        )}
      </main>
    </div>
  )
}
