// The entries an author opened most recently in each project, so they can be
// offered again wherever something is being chosen. Kept in this browser only.
const KEY = 'novel_recent_entries'
const LIMIT = 8

function read(): Record<string, string[]> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return value && typeof value === 'object' ? (value as Record<string, string[]>) : {}
  } catch {
    return {}
  }
}

export function rememberEntry(projectId: string, entityId: string): void {
  try {
    const all = read()
    const list = (all[projectId] ?? []).filter((item) => item !== entityId)
    all[projectId] = [entityId, ...list].slice(0, LIMIT)
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    // Storage can be unavailable (private browsing); the list is a convenience only.
  }
}

export function recentEntries(projectId: string): string[] {
  const list = read()[projectId]
  return Array.isArray(list) ? list.filter((item) => typeof item === 'string') : []
}
