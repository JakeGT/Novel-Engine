import type { CheckPass, CheckRun, CheckRunFinding, CheckRunScene } from '../api/client'
import { PASS_NAMES } from './studio'

/** What a run was, in words: "Checks without the assistant", "Audit of “The wait”". */
export function runLabel(run: CheckRun): string {
  if (run.kind === 'audit') return `Audit of “${run.title}”`
  if (run.kind === 'assistant') {
    const passes = run.passes.filter((item) => item !== 'model_free') as CheckPass[]
    return `Check with the assistant: ${passes.map((item) => PASS_NAMES[item] ?? item).join(', ')}`
  }
  return 'Checks without the assistant'
}

const STATUS_WORDS: Record<string, string> = {
  open: 'Still open',
  accepted: 'Kept on purpose',
  dismissed: 'Dismissed',
  superseded: 'Found again by a later check',
  fixed: 'Fixed',
}

const REASONS: Record<string, string> = {
  intended: 'intended',
  not_a_problem: 'not a problem',
}

/** What became of one finding, in words. */
export function findingStatus(finding: CheckRunFinding): string {
  if (finding.status === 'fixed') {
    return finding.fixed_by === 'story' ? 'Fixed: the story changed, so it no longer holds' : 'Fixed: the words changed'
  }
  if (finding.status === 'accepted') {
    return finding.dismissal?.note ? `Kept on purpose: ${finding.dismissal.note}` : 'Kept on purpose'
  }
  if (finding.status === 'dismissed' && finding.dismissal) {
    const why = finding.dismissal.note || REASONS[finding.dismissal.reason] || ''
    return why ? `Dismissed: ${why}` : 'Dismissed'
  }
  if (finding.came_back) return 'Back again: what it says has changed since you dismissed it'
  return finding.stale ? 'Still open, though it may no longer hold' : STATUS_WORDS[finding.status]
}

/** How many findings came to what, leaving out what nothing came to. */
export function tallyText(part: {
  open: number
  accepted?: number
  dismissed: number
  superseded?: number
  fixed: number
}): string {
  const parts: [number, string][] = [
    [part.open, 'open'],
    [part.accepted ?? 0, 'kept on purpose'],
    [part.dismissed, 'dismissed'],
    [part.superseded ?? 0, 'found again later'],
    [part.fixed, 'fixed'],
  ]
  const said = parts.filter(([count]) => count > 0).map(([count, words]) => `${count} ${words}`)
  return said.length > 0 ? said.join(' · ') : 'Nothing found'
}

const PROFILE_WORDS: Record<string, string> = {
  on_request: 'checks with the assistant only when you ask',
  balanced: 'checks with the assistant after you keep a draft',
  thorough: 'checks with the assistant after you keep a draft or save',
}

/** How much the story checked on its own when the run was made, when that was more than
 * only on request (the default, not worth repeating on every record). */
export function profileText(profile: Record<string, string>): string | null {
  if (!profile.checking || profile.checking === 'on_request') return null
  const said = PROFILE_WORDS[profile.checking]
  return said ? `Then, the story ${said}.` : null
}

function listed(names: string[]): string {
  return names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** Which checks this run worked out, and which it took as they were, in one short line. */
export function workText(scene: CheckRunScene): string | null {
  const checks = scene.checks ?? []
  if (checks.length === 0) return null
  const again = checks.filter((check) => !check.reused).map((check) => check.words.toLowerCase())
  const kept = checks.filter((check) => check.reused).map((check) => check.words.toLowerCase())
  if (again.length === 0) return 'Nothing these checks read had changed, so none was worked out again.'
  if (kept.length === 0) return `All ${checks.length} checks were worked out.`
  if (kept.length <= again.length) {
    return `${again.length} of ${checks.length} checks were worked out again; ${listed(kept)} read nothing that had changed.`
  }
  return `Only ${listed(again)} ${again.length === 1 ? 'was' : 'were'} worked out again; the other ${kept.length} read nothing that had changed.`
}

export function versionNumbers(versions: { id: string; number: number }[]): Map<string, number> {
  return new Map(versions.map((version) => [version.id, version.number]))
}

/** A time in plain words, in seconds: "under 0.001 seconds", "0.012 seconds", "3.4 seconds". */
export function secondsText(ms: number): string {
  if (ms < 1) return 'under 0.001 seconds'
  const seconds = ms / 1000
  if (seconds < 0.1) return `${seconds.toFixed(3)} seconds`
  if (seconds < 10) return `${seconds.toFixed(seconds < 1 ? 2 : 1)} seconds`
  return `${Math.round(seconds)} seconds`
}

/** How one pass with the assistant was sent, in a few words. */
export function passTimingText(item: { calls: number; earlier?: boolean }): string {
  if (item.earlier) return 'Finished by an earlier try: nothing sent again'
  if (item.calls === 0) return 'Nothing to send'
  return item.calls === 1 ? 'One request' : `${item.calls} requests`
}
