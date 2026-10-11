import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { isTextEvidence, parseLocation } from '../lib/texts'
import { scopedKey, useProject } from '../project'
import { Badge } from './ui'

interface Evidence {
  source_artefact_id: string
  location?: string | null
}

/**
 * The words of the author's own text that a change came from. Shown wherever a
 * change is reviewed or explained, so nothing has to be taken on trust.
 */
export function FromText({ evidence }: { evidence: Evidence[] | undefined }) {
  const ref = (evidence ?? []).find((item) => isTextEvidence(item.source_artefact_id))
  if (!ref) return null
  return <Quoted versionId={ref.source_artefact_id} location={ref.location ?? null} />
}

function Quoted({ versionId, location }: { versionId: string; location: string | null }) {
  const { scope } = useProject()
  const passage = useQuery({
    queryKey: scopedKey(scope, 'passage', versionId, location ?? ''),
    queryFn: () => api.passage(scope, versionId, location),
    staleTime: Infinity,
    retry: false,
  })
  const place = parseLocation(location)
  if (passage.isPending) return null
  if (passage.error) {
    const gone = passage.error instanceof ApiError && passage.error.status === 404
    return (
      <p className="from-text muted small">
        {gone ? 'From a text you have since removed.' : 'The words this came from could not be shown.'}
      </p>
    )
  }
  const found = passage.data
  const title = found.title || 'your text'
  const link = (
    <Link to={`/projects/${scope.projectId}/texts/${found.source_id}${place ? `#at-${place.start}-${place.end}` : ''}`}>
      {title}
    </Link>
  )
  if (!found.quote) return <p className="from-text muted small">From {link}.</p>
  return (
    <figure className="from-text">
      <blockquote>
        <span className="muted">…{found.before.replace(/\s+/g, ' ')}</span>
        <mark>{found.quote}</mark>
        <span className="muted">{found.after.replace(/\s+/g, ' ')}…</span>
      </blockquote>
      <figcaption className="small muted">
        From {link}
        {place?.implied && (
          <>
            {' '}
            <Badge tone="warn">Implied, not stated</Badge>
          </>
        )}
      </figcaption>
    </figure>
  )
}
