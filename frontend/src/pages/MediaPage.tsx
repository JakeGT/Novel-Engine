import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Asset } from '../api/client'
import { Icon } from '../components/icons'
import { Badge, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { fullDate, timeAgo } from '../lib/time'
import { words } from '../lib/words'
import { actorLabel, scopedKey, useEntries, useProject } from '../project'

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function AssetDetail({ asset }: { asset: Asset }) {
  const { scope, userId } = useProject()
  const entries = useEntries(scope)
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
    <div className="stack" style={{ padding: '0 1.25rem 1.25rem' }}>
      <ErrorNotice error={versions.error ?? lifecycle.error ?? links.error} />
      <div>
        <h2>Versions</h2>
        <ul className="rows" style={{ marginTop: '0.5rem' }}>
          {(versions.data?.items ?? []).map((version) => (
            <li className="row" key={version.id}>
              <span className="grow">
                <strong>Version {version.version}</strong>{' '}
                <span className="muted">
                  {version.content_type} · {size(version.byte_length)}
                </span>
              </span>
              <span className="muted small">
                {version.licence ? `Licence: ${version.licence}` : 'No licence recorded'}
                {version.attribution ? ` · ${version.attribution}` : ''}
              </span>
              {version.retired_revision != null && <Badge tone="danger">Removed</Badge>}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h2>Linked to</h2>
        <ul className="rows" style={{ marginTop: '0.5rem' }}>
          {(links.data?.items ?? []).map((link) => (
            <li className="row" key={link.id}>
              <span className="grow">
                {entries.names.get(link.target_record_id) ?? `A ${link.target_record_type}`}
              </span>
              <Badge>{link.role}</Badge>
              {link.retired_revision != null && <Badge tone="danger">Removed</Badge>}
            </li>
          ))}
          {links.data?.items.length === 0 && <li className="muted">Not linked to anything.</li>}
        </ul>
      </div>
      <details className="more">
        <summary>Activity</summary>
        <ol className="timeline">
          {(lifecycle.data?.items ?? []).map((event, index) => (
            <li key={index}>
              {words(event.action)}
              <div className="when" title={fullDate(event.created_at)}>
                {event.actor_user_id ? `${actorLabel(event.actor_user_id, userId)} · ` : ''}
                {event.canon_revision != null ? `version ${event.canon_revision} · ` : ''}
                {timeAgo(event.created_at)}
              </div>
            </li>
          ))}
        </ol>
      </details>
    </div>
  )
}

/** Registered media and what it is linked to. Read-only: files are not uploaded here. */
export function MediaPage() {
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
    <>
      <div className="page-head">
        <div>
          <h1>Media</h1>
          <p className="lede">
            The pictures and documents in this project, and the entries they belong to. To add a
            picture, open an entry and choose “Add picture”; adding files from this page is not
            available yet.
          </p>
        </div>
      </div>
      <div className="section-gap">
        <ErrorNotice error={assets.error} prefix="Could not load media" />
        {assets.isPending && <Loading>Loading media…</Loading>}
        {assets.isSuccess && items.length === 0 && (
          <EmptyState icon="image" title="No media yet">
            Nothing has been added to this project. Open an entry and choose “Add picture”.
          </EmptyState>
        )}
        <ul className="stack">
          {items.map((asset) => {
            const open = openId === asset.id
            return (
              <li key={asset.id} id={asset.id} className="card flush">
                <button
                  type="button"
                  className="row"
                  style={{
                    width: '100%',
                    padding: '0.9rem 1.25rem',
                    border: 0,
                    background: 'transparent',
                    color: 'inherit',
                    font: 'inherit',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? null : asset.id)}
                >
                  <span className="avatar sm tint-blue" aria-hidden="true">
                    <Icon name="image" size={15} />
                  </span>
                  <strong className="grow">{asset.name}</strong>
                  {asset.lifecycle === 'active' ? (
                    <Badge tone="ok">Active</Badge>
                  ) : (
                    <Badge tone="danger">{words(asset.lifecycle)}</Badge>
                  )}
                </button>
                {open && <AssetDetail asset={asset} />}
              </li>
            )
          })}
        </ul>
      </div>
    </>
  )
}
