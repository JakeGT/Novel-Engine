import { useQuery } from '@tanstack/react-query'
import { api, type SuggestionRun, type ThreadKind } from '../api/client'
import { scopedKey, useProject } from '../project'
import { plural } from './words'

/** The signed-in author's kept conversation of one kind, shared by every screen showing it. */
export function useKeptThread(kind: ThreadKind) {
  const { scope } = useProject()
  return useQuery({
    queryKey: scopedKey(scope, 'assist-thread', kind),
    queryFn: () => api.assistThread(scope, kind),
  })
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** One run of suggestions, in a sentence: how many were suggested and what became of them. */
export function runSentence(run: SuggestionRun): string {
  const parts: string[] = [plural(run.suggested, 'suggestion')]
  if (run.kind !== 'plan_next') {
    if (!run.decided) parts.push('waiting for your review')
    else if (run.kept_for_you) {
      // Kept by a keep-for-me rule: the author decided nothing, so it is not their choice.
      parts.push(`${run.kept_for_you} kept for you by your rule`)
    } else if (run.kept !== null && run.precision !== null) {
      parts.push(`${run.kept} kept (${percent(run.precision)})`)
    }
  }
  if (run.grounded !== null && run.invented !== null && run.grounded + run.invented > 0) {
    parts.push(
      `${run.grounded} from your words, ${run.invented} the assistant’s own` +
        (run.groundedness !== null ? ` (${percent(run.groundedness)} from your words)` : ''),
    )
  }
  if (run.kind === 'links') {
    parts.push(run.source === 'meaning' ? 'close pairs found by meaning first' : 'from the listing')
  }
  return parts.join(' · ')
}
