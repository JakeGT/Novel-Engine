// Where a remembered answer about a name holds (CTX-016): everywhere, in one text, in a
// stretch of scenes, in scenes seen by one character, and in or beyond a what-if. The
// server decides which answer applies; these helpers only say it in words and build the
// request.

export type Only = 'everywhere' | 'text' | 'from' | 'until' | 'between' | 'seen_by'

export interface NameLimit {
  only: Only
  fromScene: string
  untilScene: string
  viewpoint: string
  /** Inside a what-if: keep the answer to it (the default there). */
  whatIfOnly: boolean
}

export interface LimitBody {
  source_id: string | null
  what_if_only: boolean
  from_scene_id: string | null
  until_scene_id: string | null
  viewpoint_id: string | null
}

export function defaultLimit(inWhatIf: boolean, sceneId?: string | null): NameLimit {
  return { only: 'everywhere', fromScene: sceneId ?? '', untilScene: sceneId ?? '', viewpoint: '', whatIfOnly: inWhatIf }
}

/** What is missing before the answer can be sent, in plain words; null when ready. */
export function limitProblem(limit: NameLimit, order: string[]): string | null {
  const needsFrom = limit.only === 'from' || limit.only === 'between'
  const needsUntil = limit.only === 'until' || limit.only === 'between'
  if (needsFrom && !limit.fromScene) return 'Choose the first scene it holds in.'
  if (needsUntil && !limit.untilScene) return 'Choose the last scene it holds in.'
  if (limit.only === 'between' && order.indexOf(limit.fromScene) > order.indexOf(limit.untilScene)) {
    return 'The first scene comes after the last one. Swap them.'
  }
  if (limit.only === 'seen_by' && !limit.viewpoint) return 'Choose whose eyes the scenes are seen through.'
  return null
}

export function limitBody(limit: NameLimit, { textId, inWhatIf }: { textId?: string; inWhatIf: boolean }): LimitBody {
  const from = limit.only === 'from' || limit.only === 'between'
  const until = limit.only === 'until' || limit.only === 'between'
  return {
    source_id: limit.only === 'text' && textId ? textId : null,
    what_if_only: inWhatIf && limit.whatIfOnly,
    from_scene_id: from ? limit.fromScene || null : null,
    until_scene_id: until ? limit.untilScene || null : null,
    viewpoint_id: limit.only === 'seen_by' ? limit.viewpoint || null : null,
  }
}

export interface LimitedAnswer {
  branch_id?: string | null
  source_id?: string | null
  from_scene_id?: string | null
  until_scene_id?: string | null
  viewpoint_id?: string | null
}

interface Words {
  scenes: Record<string, string>
  names: Record<string, string>
  texts?: Record<string, string>
}

const GONE = 'a scene no longer in the plan'

/** Where an answer holds, as a sentence fragment: "From “Noon” on, when seen by Tobin". */
export function describeLimit(answer: LimitedAnswer, words: Words): string {
  const scene = (id: string) => (words.scenes[id] ? `“${words.scenes[id]}”` : GONE)
  const parts: string[] = []
  if (answer.branch_id) parts.push('in this what-if')
  if (answer.source_id) {
    const title = words.texts?.[answer.source_id]
    parts.push(title ? `in “${title}”` : 'in one text')
  }
  const { from_scene_id: from, until_scene_id: until } = answer
  if (from && until) parts.push(from === until ? `in ${scene(from)}` : `from ${scene(from)} to ${scene(until)}`)
  else if (from) parts.push(words.scenes[from] ? `from ${scene(from)} on` : `from ${GONE}`)
  else if (until) parts.push(`until ${scene(until)}`)
  if (answer.viewpoint_id) parts.push(`when seen by ${words.names[answer.viewpoint_id] ?? 'a character no longer in the story'}`)
  if (parts.length === 0) return 'Everywhere'
  const text = parts.join(', ')
  return `Only ${text}`
}

/** Whether an answer names a scene that is gone, so it no longer applies anywhere. */
export function limitIsBroken(answer: LimitedAnswer, scenes: Record<string, string>): boolean {
  return [answer.from_scene_id, answer.until_scene_id].some((id) => Boolean(id) && !scenes[id as string])
}
