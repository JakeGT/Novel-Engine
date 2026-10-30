import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { api, type SessionInfo } from '../api/client'
import { setActiveWorkspaceId } from '../scope'

export function useSession() {
  return useQuery({ queryKey: ['session'], queryFn: api.session })
}

export function displayName(session: SessionInfo | undefined): string {
  const user = session?.user
  return user?.display_name || user?.email || 'You'
}

export function firstName(session: SessionInfo | undefined): string {
  // Local mode has a placeholder account, not a person to greet by name.
  if (session?.auth_mode === 'local') return ''
  const name = session?.user.display_name?.trim()
  return name ? name.split(/\s+/)[0] : ''
}

/** End the session here and, in hosted mode, at the identity provider too. */
export function useSignOut(): { signOut: () => Promise<void>; pending: boolean } {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const signOut = useCallback(async () => {
    setPending(true)
    let target = '/'
    try {
      const result = await api.logout()
      if (result.logout_url) target = result.logout_url
    } catch {
      // An expired session is already signed out; fall through to the front page.
    }
    setActiveWorkspaceId(null)
    queryClient.clear()
    window.location.assign(target)
  }, [queryClient])
  return { signOut, pending }
}
