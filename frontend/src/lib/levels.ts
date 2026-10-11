// What a project shows by default. A level is not a gate: every page stays one
// click away under "More" and in search. It only keeps a brand-new project's menu
// to what a newcomer needs first. See plan/19-assistant-first-experience.md.
import { useCallback, useSyncExternalStore } from 'react'

export type PageId = 'plan' | 'texts' | 'rules' | 'history' | 'customize' | 'media'

/** A project with fewer entries than this is still getting its first idea down. */
export const SHAPING_FROM = 3

const SHOW_ALL = 'novel-engine.menu.show-all'
const visitedKey = (projectId: string) => `novel-engine.menu.visited.${projectId}`

function read(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Without storage the menu simply stays at its defaults.
  }
  window.dispatchEvent(new Event('novel-engine:menu'))
}

function subscribe(listener: () => void): () => void {
  window.addEventListener('novel-engine:menu', listener)
  window.addEventListener('storage', listener)
  return () => {
    window.removeEventListener('novel-engine:menu', listener)
    window.removeEventListener('storage', listener)
  }
}

/** Remembers that the author reached for a page, so it stays in their menu. */
export function rememberVisit(projectId: string, page: PageId): void {
  const visited = new Set(read(visitedKey(projectId)).split(',').filter(Boolean))
  if (visited.has(page)) return
  visited.add(page)
  write(visitedKey(projectId), [...visited].join(','))
}

/**
 * Which of the later pages sit in the menu itself. History and Media appear once
 * the project has a handful of entries; the story plan and the author's own writing
 * once there is some or the author has opened the page; Types and fields once the author has opened it or made a
 * type of their own. An author can ask for everything.
 */
export function useMenu(
  projectId: string,
  entryCount: number,
  /** Pages the project is already using, which therefore belong in its menu. */
  inUse: Partial<Record<PageId, boolean>>,
) {
  const snapshot = useSyncExternalStore(
    subscribe,
    () => `${read(SHOW_ALL)}|${read(visitedKey(projectId))}`,
    () => '|',
  )
  const [all, visitedText] = snapshot.split('|')
  const showAll = all === 'yes'
  const visited = new Set(visitedText.split(',').filter(Boolean))
  const shaping = entryCount >= SHAPING_FROM
  const shown = (page: PageId): boolean =>
    showAll ||
    visited.has(page) ||
    Boolean(inUse[page]) ||
    // The plan, the author's texts, rules and the setup appear once used or opened; the rest once
    // the project has grown.
    (page !== 'customize' && page !== 'plan' && page !== 'texts' && page !== 'rules' && shaping)
  const setShowAll = useCallback((value: boolean) => write(SHOW_ALL, value ? 'yes' : 'no'), [])
  return { shown, showAll, setShowAll }
}
