import type { CheckPass, CheckRun, CheckRunFinding } from '../api/client'
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
  dismissed: 'Dismissed',
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
  if (finding.status === 'dismissed' && finding.dismissal) {
    const why = finding.dismissal.note || REASONS[finding.dismissal.reason] || ''
    return why ? `Dismissed: ${why}` : 'Dismissed'
  }
  return finding.stale ? 'Still open, though it may no longer hold' : STATUS_WORDS[finding.status]
}

export function versionNumbers(versions: { id: string; number: number }[]): Map<string, number> {
  return new Map(versions.map((version) => [version.id, version.number]))
}
