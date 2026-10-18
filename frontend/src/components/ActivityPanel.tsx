import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { isKeptNote } from '../lib/kept'
import { isTextEvidence } from '../lib/texts'
import { fullDate, timeAgo } from '../lib/time'
import { originLabel } from '../lib/words'
import { actorLabel, scopedKey, useProject } from '../project'
import { FromText } from './FromText'
import { ErrorNotice } from './ui'

const VERBS: Record<string, string> = {
  create_entity: 'Added',
  update_entity: 'Edited',
  retire_entity: 'Archived',
  assert_fact: 'Fact added',
  close_fact: 'Fact ended',
  assert_belief: 'Belief added',
  close_belief: 'Belief ended',
  create_event: 'Event added',
  amend_event: 'Event edited',
  amend_fact: 'When or who knows changed',
  retire_event: 'Event archived',
}

/** An author's own small edit is approved automatically; that is not a review worth listing. */
function reviewed(decision: { note: string | null }): boolean {
  return decision.note !== 'author-direct'
}

/** Why each change to a record exists: who made it, where it came from, how it was reviewed. */
export function ActivityPanel({ recordType, recordId }: { recordType: string; recordId: string }) {
  const { scope, userId } = useProject()
  const provenance = useQuery({
    queryKey: scopedKey(scope, 'provenance', recordType, recordId),
    queryFn: () => api.provenance(scope, recordType, recordId),
  })
  const entries = provenance.data?.items ?? []
  const redacted = entries.some((entry) => entry.redacted)

  return (
    <section className="card" aria-labelledby="activity-heading">
      <div className="card-head">
        <h2 id="activity-heading">Activity</h2>
      </div>
      <ErrorNotice error={provenance.error} prefix="Could not load the activity" />
      {redacted && (
        <p className="notice plain small" role="note" style={{ marginBottom: '0.9rem' }}>
          Reviewer names and notes are hidden for your role. You can still see where each change
          came from.
        </p>
      )}
      <ol className="timeline">
        {[...entries].reverse().map((entry) => {
          const who = entry.redacted ? '' : actorLabel(entry.actor_user_id, userId)
          const source =
            entry.origin && entry.origin !== 'author'
              ? `from ${originLabel(entry.origin)}`
              : who
                ? `by ${who}`
                : ''
          return (
            <li key={`${entry.commit_id}-${entry.operation_type}-${entry.revision}`}>
              <strong>{VERBS[entry.operation_type] ?? entry.operation_type}</strong>
              {entry.intent === 'revert' ? ' (restoring an earlier version)' : ''} {source}
              <div className="when" title={fullDate(entry.created_at)}>
                Version {entry.revision} · {timeAgo(entry.created_at)}
              </div>
              {entry.decisions.filter(reviewed).map((decision, index) => (
                <div className="small muted" key={`d${index}`}>
                  {isKeptNote(decision.note) && !entry.redacted ? decision.note : <>Reviewed: {decision.decision}
                  {entry.redacted
                    ? ' (reviewer and note hidden)'
                    : `${decision.actor_user_id ? ` by ${actorLabel(decision.actor_user_id, userId)}` : ''}${
                        decision.note ? ` — “${decision.note}”` : ''
                      }`}</>}
                </div>
              ))}
              <FromText evidence={entry.evidence} />
              {entry.evidence
                .filter((item) => !isTextEvidence(item.source_artefact_id))
                .map((item, index) => (
                  <div className="small muted" key={`e${index}`}>
                    Source: <code>{item.source_artefact_id}</code>
                    {item.location ? ` at ${item.location}` : ''}
                  </div>
                ))}
            </li>
          )
        })}
      </ol>
      {entries.length === 0 && !provenance.isPending && !provenance.isError && (
        <p className="muted">No recorded changes.</p>
      )}
    </section>
  )
}
