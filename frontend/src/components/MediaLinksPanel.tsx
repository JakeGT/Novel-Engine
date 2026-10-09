import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'
import { Badge, ErrorNotice } from './ui'

/** Explicit, reviewed evidence links from media assets to this record (metadata only). */
export function MediaLinksPanel({ targetId }: { targetId: string }) {
  const { scope } = useProject()
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', targetId),
    queryFn: () => api.listLinks(scope, { target_id: targetId, include_retired: true }),
  })
  const items = links.data?.items ?? []
  return (
    <section className="panel" aria-labelledby="media-heading">
      <h3 id="media-heading">Linked media</h3>
      <ErrorNotice error={links.error} prefix="Could not load media links" />
      {items.length === 0 && !links.isLoading && <p className="muted">No media is linked.</p>}
      <ul className="list">
        {items.map((link) => (
          <li key={link.id}>
            <Badge>{link.role}</Badge>{' '}
            <Link to={`/projects/${scope.projectId}/assets#${link.asset_id}`}>{link.asset_id}</Link>{' '}
            <span className="muted">linked at r{link.created_revision}</span>{' '}
            {link.retired_revision != null && <Badge tone="danger">Retired</Badge>}
          </li>
        ))}
      </ul>
    </section>
  )
}
