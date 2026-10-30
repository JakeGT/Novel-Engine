// Which what-if the author is working in, per project (ADR-020). Kept in the browser so
// it survives a reload; the server checks the what-if belongs to the project on every
// request, so a stale choice can only ever fail closed, never touch another story.
import { useCallback, useSyncExternalStore } from 'react'

export interface WorkingWhatIf {
  id: string
  name: string
}

const key = (projectId: string) => `novel-engine.what-if.${projectId}`
const EVENT = 'novel-engine:what-if'

function read(projectId: string): string {
  try {
    return window.localStorage.getItem(key(projectId)) ?? ''
  } catch {
    return ''
  }
}

function subscribe(listener: () => void): () => void {
  window.addEventListener(EVENT, listener)
  window.addEventListener('storage', listener)
  return () => {
    window.removeEventListener(EVENT, listener)
    window.removeEventListener('storage', listener)
  }
}

export function setWorkingWhatIf(projectId: string, whatIf: WorkingWhatIf | null): void {
  try {
    if (whatIf) window.localStorage.setItem(key(projectId), JSON.stringify(whatIf))
    else window.localStorage.removeItem(key(projectId))
  } catch {
    // Without storage the author simply stays in the story itself.
  }
  window.dispatchEvent(new Event(EVENT))
}

function parse(raw: string): WorkingWhatIf | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<WorkingWhatIf>
    return typeof value.id === 'string' && typeof value.name === 'string'
      ? { id: value.id, name: value.name }
      : null
  } catch {
    return null
  }
}

/** The what-if this project is being worked in, or null for the story itself. */
export function useWorkingWhatIf(projectId: string) {
  const raw = useSyncExternalStore(
    subscribe,
    () => read(projectId),
    () => '',
  )
  const whatIf = parse(raw)
  const set = useCallback((value: WorkingWhatIf | null) => setWorkingWhatIf(projectId, value), [projectId])
  return { whatIf, set }
}
