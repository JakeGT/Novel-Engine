// The active workspace is kept in sessionStorage so project routes can scope every
// request (and cache key) without re-deriving it. It is never a security boundary:
// the server re-authorizes membership from the session on every request.
const KEY = 'novel_active_workspace'

export function getActiveWorkspaceId(): string | null {
  return sessionStorage.getItem(KEY)
}

export function setActiveWorkspaceId(workspaceId: string | null): void {
  if (workspaceId) sessionStorage.setItem(KEY, workspaceId)
  else sessionStorage.removeItem(KEY)
}
