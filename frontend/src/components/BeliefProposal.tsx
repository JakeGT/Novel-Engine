import { Link } from 'react-router-dom'
import type { Telling } from '../api/client'
import { firmness } from '../lib/knowledge'
import { plural } from '../lib/words'
import { useEntries, useProject } from '../project'
import { Badge } from './ui'

const HOW: Record<string, string> = { told: 'Told', overheard: 'Overheard', saw: 'Saw it' }

/**
 * What a telling, a sighting or something said would make each person believe, with the
 * reason for each. Nothing is in the story yet: it waits in review.
 */
export function BeliefProposal({ telling }: { telling: Telling }) {
  const { scope } = useProject()
  const { names } = useEntries(scope)
  const count = telling.beliefs
  return (
    <div className="stack">
      <p>
        {count > 0
          ? `Suggested: ${plural(count, 'new belief')}, waiting for your review. Nothing is in your story until you approve it.`
          : 'Nobody would come to believe anything new. The event still waits for your review.'}
      </p>
      <ul className="rows" aria-label="What each person would believe">
        {telling.proposals.map((item) => (
          <li className="row" key={item.holder_id}>
            <span className="grow">
              <strong>{names.get(item.holder_id) ?? 'Someone'}</strong>
              <span className="muted small" style={{ display: 'block' }}>
                {item.why}
              </span>
            </span>
            <span className="knowledge-tag">{HOW[item.how] ?? item.how}</span>
            {item.confidence === null ? (
              <Badge tone="neutral">{item.already ? 'Already believes it' : 'No belief'}</Badge>
            ) : (
              <Badge tone="info">{firmness(item.confidence)}</Badge>
            )}
          </li>
        ))}
      </ul>
      <div className="actions end">
        <Link className="btn primary" data-autofocus to={`/projects/${scope.projectId}/review/${telling.mutation_id}`}>
          Review the suggestion
        </Link>
      </div>
    </div>
  )
}
