import { useQuery } from '@tanstack/react-query'
import { api, type PlanGrounding, type SuggestionRunKind } from '../api/client'
import { runSentence } from '../lib/threads'
import { timeAgo } from '../lib/time'
import { scopedKey, useProject } from '../project'

/**
 * How earlier suggestions of one kind fared: how many were suggested, how many the author
 * kept once decided, and how many quote the author's own material. Counts only.
 */
export function SuggestionRecord({ kind, title }: { kind: SuggestionRunKind; title: string }) {
  const { scope } = useProject()
  const runs = useQuery({
    queryKey: scopedKey(scope, 'suggestion-runs', kind),
    queryFn: () => api.suggestionRuns(scope, kind, 5),
  })
  const items = runs.data?.items ?? []
  if (items.length === 0) return null
  return (
    <section className="stack tight" aria-label={title}>
      <p className="label">{title}</p>
      <ul className="run-records">
        {items.map((run) => (
          <li key={run.id}>
            <span className="muted">{timeAgo(run.created_at)}</span>
            <span>{runSentence(run)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Where each suggested part of the plan comes from: the author's words, or the assistant. */
export function GroundingList({ items }: { items: PlanGrounding[] }) {
  if (items.length === 0) return null
  const grounded = items.filter((item) => item.grounded).length
  return (
    <div className="stack tight">
      <p>
        <strong>Where each part comes from:</strong> {grounded} of {items.length} quote what you
        wrote; the rest are the assistant’s own suggestions.
      </p>
      <ul className="grounding">
        {items.map((item, index) => (
          <li key={index}>
            <span>“{item.title}”</span>
            {item.grounded && item.quote ? (
              <span className="small">
                From your words: <q>{item.quote}</q>
              </span>
            ) : (
              <span className="small muted">The assistant’s own suggestion, not from anything you wrote.</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
