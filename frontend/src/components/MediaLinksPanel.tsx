import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'
import { Badge, ErrorNotice } from './ui'

/** Media that has been linked to this record. Shown only when there is some. */
export function MediaLinksPanel({ targetId }: { targetId: string }) {
  const { scope } = useProject()
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', targetId),
    queryFn: () => api.listLinks(scope, { target_id: targetId, include_retired: true }),
  })
  const assets = useQuery({
    queryKey: scopedKey(scope, 'assets'),
    queryFn: () => api.listAssets(scope),
    enabled: (links.data?.items.length ?? 0) > 0,
  })
  const items = links.data?.items ?? []
  if (items.length === 0 && !links.isError) return null
  const names = new Map((assets.data?.items ?? []).map((asset) => [asset.id, asset.name]))
  return (
    <section className="card" aria-labelledby="media-heading">
      <div className="card-head">
        <h2 id="media-heading">Linked media</h2>
      </div>
      <ErrorNotice error={links.error} prefix="Could not load linked media" />
      <ul className="rows">
        {items.map((link) => (
          <li className="row" key={link.id}>
            <span className="grow">
              <Link to={`/projects/${scope.projectId}/media#${link.asset_id}`}>
                {names.get(link.asset_id) ?? 'Media item'}
              </Link>
            </span>
            <Badge>{link.role}</Badge>
            {link.retired_revision != null && <Badge tone="danger">Removed</Badge>}
          </li>
        ))}
      </ul>
    </section>
  )
}
