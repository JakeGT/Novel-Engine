import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Asset } from '../api/client'
import { Badge, ErrorNotice } from '../components/ui'
import { scopedKey, useProject } from '../project'

function AssetDetail({ asset }: { asset: Asset }) {
  const { scope } = useProject()
  const versions = useQuery({
    queryKey: scopedKey(scope, 'asset-versions', asset.id),
    queryFn: () => api.listAssetVersions(scope, asset.id),
  })
  const lifecycle = useQuery({
    queryKey: scopedKey(scope, 'asset-lifecycle', asset.id),
    queryFn: () => api.assetLifecycle(scope, asset.id),
  })
  const links = useQuery({
    queryKey: scopedKey(scope, 'asset-links', asset.id),
    queryFn: () => api.listAssetLinks(scope, asset.id),
  })

  return (
    <div className="nested">
      <ErrorNotice error={versions.error ?? lifecycle.error ?? links.error} />
      <h3>Versions</h3>
      <ul className="list">
        {(versions.data?.items ?? []).map((version) => (
          <li key={version.id}>
            <strong>v{version.version}</strong> {version.content_type} ·{' '}
            {version.byte_length.toLocaleString()} bytes{' '}
            <span className="muted">
              {version.licence ? `licence ${version.licence}` : 'no licence recorded'}
              {version.attribution ? ` · ${version.attribution}` : ''}
            </span>{' '}
            {version.retired_revision != null && <Badge tone="danger">Retired</Badge>}
          </li>
        ))}
      </ul>
      <h3>Linked to</h3>
      <ul className="list">
        {(links.data?.items ?? []).map((link) => (
          <li key={link.id}>
            <Badge>{link.role}</Badge> {link.target_record_type} <code>{link.target_record_id}</code>{' '}
            {link.retired_revision != null && <Badge tone="danger">Retired</Badge>}
          </li>
        ))}
        {links.data?.items.length === 0 && <li className="muted">No links.</li>}
      </ul>
      <h3>Lifecycle audit</h3>
      <ol className="list timeline">
        {(lifecycle.data?.items ?? []).map((event, index) => (
          <li key={index}>
            {event.action.replace(/_/g, ' ')}
            <span className="muted">
              {event.actor_user_id ? ` · ${event.actor_user_id}` : ''}
              {event.canon_revision != null ? ` · at r${event.canon_revision}` : ''} ·{' '}
              {new Date(event.created_at).toLocaleString()}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

export function AssetsPage() {
  const { scope } = useProject()
  const [openId, setOpenId] = useState<string | null>(
    window.location.hash ? window.location.hash.slice(1) : null,
  )
  const assets = useQuery({
    queryKey: scopedKey(scope, 'assets'),
    queryFn: () => api.listAssets(scope),
  })
  const items = assets.data?.items ?? []

  return (
    <section className="panel" aria-labelledby="assets-heading">
      <h2 id="assets-heading">Media</h2>
      <p className="muted">
        Registered media and how it is linked to canon. This shows identity and provenance only:
        files are not uploaded, scanned, or processed here.
      </p>
      <ErrorNotice error={assets.error} prefix="Could not load media" />
      {items.length === 0 && !assets.isLoading && <p className="muted">No media is registered.</p>}
      <ul className="list">
        {items.map((asset) => {
          const open = openId === asset.id
          return (
            <li key={asset.id} id={asset.id}>
              <button
                type="button"
                className="disclosure"
                aria-expanded={open}
                onClick={() => setOpenId(open ? null : asset.id)}
              >
                {asset.name}
              </button>{' '}
              {asset.lifecycle === 'active' ? (
                <Badge tone="ok">Active</Badge>
              ) : (
                <Badge tone="danger">{asset.lifecycle}</Badge>
              )}{' '}
              <code>{asset.id}</code>
              {open && <AssetDetail asset={asset} />}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
