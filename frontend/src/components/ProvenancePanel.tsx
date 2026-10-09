import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'
import { Badge, ErrorNotice } from './ui'

/** Why each change to a record exists: commit, origin, review decisions, and evidence. */
export function ProvenancePanel({ recordType, recordId }: { recordType: string; recordId: string }) {
  const { scope } = useProject()
  const provenance = useQuery({
    queryKey: scopedKey(scope, 'provenance', recordType, recordId),
    queryFn: () => api.provenance(scope, recordType, recordId),
  })
  const entries = provenance.data?.items ?? []
  const redacted = entries.some((entry) => entry.redacted)

  return (
    <section className="panel" aria-labelledby="provenance-heading">
      <h3 id="provenance-heading">History and provenance</h3>
      <ErrorNotice error={provenance.error} prefix="Could not load provenance" />
      {redacted && (
        <p className="notice" role="note">
          Reviewer names and review notes are hidden for your role. You can still see where each
          change came from and the evidence it cites.
        </p>
      )}
      <ol className="list timeline">
        {[...entries].reverse().map((entry) => (
          <li key={`${entry.commit_id}-${entry.operation_type}-${entry.revision}`}>
            <Badge>r{entry.revision}</Badge> <strong>{entry.operation_type}</strong>{' '}
            <span className="muted">
              origin {entry.origin ?? 'unknown'}
              {entry.intent && entry.intent !== 'edit' ? ` · ${entry.intent}` : ''}
              {entry.redacted
                ? ' · author hidden'
                : entry.actor_user_id
                  ? ` · by ${entry.actor_user_id}`
                  : ''}
            </span>
            {entry.decisions.length > 0 && (
              <ul>
                {entry.decisions.map((decision, index) => (
                  <li key={index}>
                    Review: {decision.decision}
                    {entry.redacted
                      ? ' (reviewer and note hidden)'
                      : `${decision.actor_user_id ? ` by ${decision.actor_user_id}` : ''}${
                          decision.note ? ` — ${decision.note}` : ''
                        }`}
                  </li>
                ))}
              </ul>
            )}
            {entry.evidence.length > 0 && (
              <ul>
                {entry.evidence.map((item, index) => (
                  <li key={index}>
                    Evidence: <code>{item.source_artefact_id}</code>
                    {item.location ? ` at ${item.location}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {entries.length === 0 && !provenance.isLoading && !provenance.isError && (
        <p className="muted">No recorded changes.</p>
      )}
    </section>
  )
}
