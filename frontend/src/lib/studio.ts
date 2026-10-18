// Helpers for the scene studio (ADR-022): where a check points in the text, the
// author's unsaved words kept in this browser, and the words the studio uses.
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type AssistantNote,
  type CheckPass,
  type DismissAsk,
  type DraftCheck,
  type PassEstimate,
  type PlanItem,
  type Scope,
  type StudioJob,
} from '../api/client'
import { scopedKey, useProject } from '../project'

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

/** Dismissing and bringing back, for one scene: the page's copy of the scene is replaced. */
export function useDismissals(planId: string) {
  const { scope } = useProject()
  const client = useQueryClient()
  const studioKey = scopedKey(scope, 'studio', planId)
  const dismiss = useMutation({
    mutationFn: (ask: DismissAsk) => api.dismissFinding(scope, planId, ask),
    onSuccess: (next) => client.setQueryData(studioKey, next),
  })
  const undo = useMutation({
    mutationFn: (dismissalId: string) => api.undoDismissal(scope, planId, dismissalId),
    onSuccess: (next) => client.setQueryData(studioKey, next),
  })
  return { dismiss, undo }
}

/** What a check with the assistant looks for, in the author's words. */
export const NOTE_KINDS: Record<string, string> = {
  continuity: 'Continuity',
  viewpoint: 'Viewpoint',
  plan: 'Against the plan',
  voice: 'Your voice',
  clarity: 'Clarity',
}

/** The passes of a check with the assistant, in the order they run (CON-008). */
export const PASS_ORDER: CheckPass[] = ['viewpoint', 'continuity', 'plan', 'voice', 'clarity']

/** What each pass is called, while the server's own words are on their way. */
export const PASS_NAMES: Record<CheckPass, string> = {
  viewpoint: 'Viewpoint and secrets',
  continuity: 'Continuity with the story bible',
  plan: 'What the plan asks for',
  voice: 'Your voice',
  clarity: 'Clarity',
}

/** A number of tokens as people read it: 12,400. */
export const tokenCount = (value: number) => value.toLocaleString('en-GB')

/** What the chosen passes would use in all, from the estimate; null while it loads. */
export function chosenTokens(passes: PassEstimate[] | undefined, chosen: CheckPass[]): number | null {
  if (!passes) return null
  return passes.filter((item) => chosen.includes(item.check_pass)).reduce((sum, item) => sum + item.total_tokens, 0)
}

/** How an audit stands, in words. */
export const AUDIT_STATUS: Record<string, string> = {
  queued: 'Waiting to start',
  running: 'Under way',
  succeeded: 'Finished',
  failed: 'Did not finish',
  cancelled: 'Stopped',
}

const passesKey = (scope: Scope) => `novel-engine.check-passes.${scope.projectId}`

/** The passes this person last chose in this story; every pass until they choose. */
export function readChosenPasses(scope: Scope): CheckPass[] {
  try {
    const raw = window.localStorage.getItem(passesKey(scope))
    if (!raw) return [...PASS_ORDER]
    const value: unknown = JSON.parse(raw)
    if (!Array.isArray(value)) return [...PASS_ORDER]
    return PASS_ORDER.filter((item) => value.includes(item))
  } catch {
    return [...PASS_ORDER]
  }
}

export function keepChosenPasses(scope: Scope, passes: CheckPass[]): void {
  try {
    window.localStorage.setItem(passesKey(scope), JSON.stringify(passes))
  } catch {
    // A browser that keeps nothing still checks; the choice just is not remembered.
  }
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

/** Reading the scene into suggested changes to the story (Phase 6). */
export const READ_JOB = 'scene_read'

export function isReadJob(job: StudioJob): boolean {
  return job.kind === READ_JOB
}

/** Filling in the scene's plan card from its prose (Phase 6). */
export const PLAN_JOB = 'scene_plan'

export function isPlanJob(job: StudioJob): boolean {
  return job.kind === PLAN_JOB
}

/** What each kind of work is, while it runs. */
export const JOB_DOING: Record<string, string> = {
  scene_draft: 'The assistant is drafting this scene',
  scene_review: 'The assistant is reading your text',
  [READ_JOB]: 'The assistant is reading the scene into your story',
  [PLAN_JOB]: 'The assistant is filling in the plan card from the scene',
  story_audit: 'Auditing this part of the story',
}

/** Where an audit's results are read (CON-011). */
export function auditPath(projectId: string, jobId: string): string {
  return `/projects/${projectId}/audits/${encodeURIComponent(jobId)}`
}

/**
 * What the scene's plan card is missing that the assistant can fill in from the prose,
 * in the author's words: a summary, a viewpoint, a place.
 */
export function missingFromCard(scene: Pick<PlanItem, 'summary' | 'refs'>): string[] {
  const missing: string[] = []
  if (!scene.summary.trim()) missing.push('summary')
  if (!scene.refs.some((ref) => ref.role === 'point_of_view')) missing.push('viewpoint')
  if (!scene.refs.some((ref) => ref.role === 'place')) missing.push('place')
  return missing
}

/** "summary", "summary or place", "summary, viewpoint or place". */
export function eitherOf(words: readonly string[]): string {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}`
}

/** The set of plan changes a finished plan-card job suggested, if it suggested any. */
export function planCardProposal(job: StudioJob): string | null {
  const found = job.result.mutation_id
  return typeof found === 'string' && found ? found : null
}

/**
 * The studio's address for a scene, opened at a passage of one version of its text: the
 * studio finds those words in the text as it is now and selects them.
 */
export function scenePath(
  projectId: string,
  sceneId: string,
  passage?: { versionId: string; start: number; end: number } | null,
): string {
  const base = `/projects/${projectId}/plan/${encodeURIComponent(sceneId)}/write`
  if (!passage) return base
  const query = new URLSearchParams({ at: `${passage.start}-${passage.end}`, version: passage.versionId })
  return `${base}?${query.toString()}`
}

/** The passage a studio address asks for, as "?at=120-180". */
export function parseAt(value: string | null): Span | null {
  const match = /^(\d+)-(\d+)$/.exec(value ?? '')
  if (!match) return null
  const start = Number(match[1])
  const end = Number(match[2])
  return end > start ? { start, end } : null
}

/**
 * Where to look at what a reading suggested: the one set of changes itself, or the
 * review queue when there are several.
 */
export function reviewPath(projectId: string, proposals: readonly string[]): string {
  const base = `/projects/${projectId}/review`
  return proposals.length === 1 ? `${base}/${encodeURIComponent(proposals[0])}` : base
}

/**
 * How many changes a reading suggested, counted from the sets of changes it made (one
 * per part of the scene read). Null until every set has been looked up.
 */
export function useSuggestedChanges(proposals: readonly string[]): number | null {
  const { scope } = useProject()
  const found = useQueries({
    queries: proposals.map((mutationId) => ({
      queryKey: scopedKey(scope, 'mutation', mutationId),
      queryFn: () => api.getMutation(scope, mutationId),
    })),
  })
  if (found.some((query) => !query.data)) return null
  return found.reduce((sum, query) => sum + (query.data?.operations.length ?? 0), 0)
}

/** The ids of the suggested changes a finished reading made, from its result. */
export function readingProposals(job: StudioJob): string[] {
  const found = job.result.proposals
  return Array.isArray(found) ? found.filter((item): item is string => typeof item === 'string') : []
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
