// Helpers for the scene studio (ADR-022): where a check points in the text, the
// author's unsaved words kept in this browser, and the words the studio uses.
import type { AssistantNote, DraftCheck, Scope, StudioJob } from '../api/client'

export interface Span {
  start: number
  end: number
}

const SURROGATE = /[\uD800-\uDFFF]/

/**
 * The server counts characters as whole characters; the browser counts some (emoji,
 * rare letters) as two. Turns a server position into a position in this string.
 */
export function browserOffset(text: string, characters: number): number {
  if (!SURROGATE.test(text)) return Math.min(characters, text.length)
  let index = 0
  for (let count = 0; count < characters && index < text.length; count += 1) {
    index += (text.codePointAt(index) ?? 0) > 0xffff ? 2 : 1
  }
  return index
}

/**
 * Where a passage of the saved text now sits in the words being edited: the same place
 * if it is unchanged there, otherwise the nearest place the same words appear, or null
 * when they are gone.
 */
export function locate(saved: string, current: string, start: number, end: number): Span | null {
  const from = browserOffset(saved, start)
  const to = browserOffset(saved, end)
  const quote = saved.slice(from, to)
  if (!quote.trim()) return null
  if (current.slice(from, to) === quote) return { start: from, end: to }
  let best = -1
  for (let at = current.indexOf(quote); at !== -1; at = current.indexOf(quote, at + 1)) {
    if (best === -1 || Math.abs(at - from) < Math.abs(best - from)) best = at
  }
  return best === -1 ? null : { start: best, end: best + quote.length }
}

/** A short quotation of a passage, cut at a word when it is long. */
export function excerpt(text: string, span: Span, most = 90): string {
  const words = text.slice(span.start, span.end).replace(/\s+/g, ' ').trim()
  if (words.length <= most) return words
  const cut = words.slice(0, most)
  const space = cut.lastIndexOf(' ')
  return `${space > most / 2 ? cut.slice(0, space) : cut}…`
}

/** Warnings come before notes; within each, the order the text reads in. */
export function orderChecks(checks: DraftCheck[]): DraftCheck[] {
  const rank = (check: DraftCheck) => (check.severity === 'warning' ? 0 : 1)
  return [...checks].sort((a, b) => rank(a) - rank(b) || (a.start ?? Infinity) - (b.start ?? Infinity))
}

export function orderNotes(notes: AssistantNote[]): AssistantNote[] {
  return [...notes].sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity))
}

/** What a check with the assistant looks for, in the author's words. */
export const NOTE_KINDS: Record<string, string> = {
  continuity: 'Continuity',
  viewpoint: 'Viewpoint',
  plan: 'Against the plan',
  voice: 'Your voice',
  clarity: 'Clarity',
}

/** How long a draft should be, as named choices rather than a number to invent. */
export const LENGTHS: { value: number; label: string }[] = [
  { value: 400, label: 'Short — about 400 words' },
  { value: 800, label: 'Usual — about 800 words' },
  { value: 1500, label: 'Long — about 1,500 words' },
  { value: 2500, label: 'Very long — about 2,500 words' },
]

export const MAX_DRAFTS = 3

/** What the author asks a draft for; kept with the page so closing the form loses nothing. */
export interface DraftChoices {
  variants: number
  words: number
  direction: string
}

export const DEFAULT_CHOICES: DraftChoices = { variants: 1, words: 800, direction: '' }
export const MAX_DIRECTION = 2000
export const MAX_VOICE = 4000

export function isFinished(job: StudioJob | undefined | null): boolean {
  return Boolean(job && ['succeeded', 'failed', 'cancelled'].includes(job.status))
}

export function isDraftJob(job: StudioJob): boolean {
  return job.kind === 'scene_draft'
}

/** A fresh key for one request to draft, so sending it twice starts the work once. */
export function newRequestKey(): string {
  const maker = globalThis.crypto as Crypto | undefined
  if (maker?.randomUUID) return maker.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

// --- the author's unsaved words, kept in this browser until they are saved --------
// Leaving the page, a crash or a closed tab never costs typing: the words come back
// the next time the scene is opened. They never leave the browser from here.

export interface Unsaved {
  text: string
  /** The saved version these words were written on top of. */
  base: string | null
}

const unsavedKey = (scope: Scope, planId: string) =>
  `novel-engine.studio.${scope.projectId}.${scope.branchId ?? 'main'}.${planId}`

export function readUnsaved(scope: Scope, planId: string): Unsaved | null {
  try {
    const raw = window.localStorage.getItem(unsavedKey(scope, planId))
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<Unsaved>
    return typeof value.text === 'string'
      ? { text: value.text, base: typeof value.base === 'string' ? value.base : null }
      : null
  } catch {
    return null
  }
}

export function keepUnsaved(scope: Scope, planId: string, unsaved: Unsaved): void {
  try {
    window.localStorage.setItem(unsavedKey(scope, planId), JSON.stringify(unsaved))
  } catch {
    // Without storage the words are only on the page until they are saved.
  }
}

export function forgetUnsaved(scope: Scope, planId: string): void {
  try {
    window.localStorage.removeItem(unsavedKey(scope, planId))
  } catch {
    // Nothing was kept.
  }
}

/**
 * Scroll a writing box so a position in it is in view: measured with a hidden copy of
 * the box, since a text box cannot say where a character sits.
 */
export function scrollAreaTo(area: HTMLTextAreaElement, index: number): void {
  const style = window.getComputedStyle(area)
  const mirror = document.createElement('div')
  const copied = [
    'fontFamily',
    'fontSize',
    'fontWeight',
    'lineHeight',
    'letterSpacing',
    'paddingTop',
    'paddingLeft',
    'paddingRight',
  ] as const
  for (const property of copied) mirror.style[property] = style[property]
  Object.assign(mirror.style, {
    position: 'absolute',
    visibility: 'hidden',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'break-word',
    boxSizing: 'border-box',
    width: `${area.clientWidth}px`,
  })
  mirror.textContent = area.value.slice(0, index)
  document.body.appendChild(mirror)
  const top = mirror.scrollHeight
  mirror.remove()
  area.scrollTop = Math.max(0, top - area.clientHeight / 3)
}
