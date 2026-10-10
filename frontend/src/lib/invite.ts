// An invitation link opened while signed out is remembered for this browser tab,
// so that after signing in the person lands back on it instead of losing it.
const KEY = 'novel_pending_invite'

export function rememberInvite(token: string): void {
  if (token) sessionStorage.setItem(KEY, token)
}

export function pendingInvite(): string | null {
  return sessionStorage.getItem(KEY)
}

export function clearPendingInvite(): void {
  sessionStorage.removeItem(KEY)
}
