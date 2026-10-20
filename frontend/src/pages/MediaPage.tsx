import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Asset, type LibraryPicture, type PictureStorage } from '../api/client'
import { Icon } from '../components/icons'
import { PictureImage } from '../components/Pictures'
import { Badge, Button, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { usePictureLibrary } from '../lib/pictures'
import { fullDate, timeAgo } from '../lib/time'
import { words } from '../lib/words'
import { actorLabel, scopedKey, useEntries, useProject } from '../project'

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

// How often a picture being prepared is checked on while its page is open.
const PREPARING_POLL_MS = 3000

/** How much room the workspace's pictures take, when there is a limit. */
function StorageLine({ storage }: { storage: PictureStorage | null | undefined }) {
  if (!storage || storage.quota_bytes <= 0) return null
  const share = storage.used_bytes / storage.quota_bytes
  return (
    <p className={`small ${share >= 0.9 ? 'error-text' : 'muted'}`}>
      Pictures in this workspace use {size(storage.used_bytes)} of {size(storage.quota_bytes)}.
      {share >= 0.9 ? ' Remove pictures you no longer need to make room for new ones.' : ''}
    </p>
  )
}

/** A word on a picture whose small copy is not ready, in the list. */
function PreparingBadge({ picture }: { picture: LibraryPicture | undefined }) {
  if (!picture?.processing || picture.processing === 'ready') return null
  if (picture.processing === 'failed') return <Badge tone="warn">Not ready</Badge>
  return <Badge>Preparing</Badge>
}

/**
 * Where a picture is in being prepared (its small copy for lists), and, if that failed,
 * why in plain words with a way to try again. The picture itself is never affected.
 */
function PictureState({ picture }: { picture: LibraryPicture }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'picture-processing', picture.version_id)
  const state = useQuery({
    queryKey: key,
    queryFn: () => api.pictureProcessing(scope, picture.asset_id, picture.version_id),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'queued' || status === 'processing' ? PREPARING_POLL_MS : false
    },
  })
  const retry = useMutation({
    mutationFn: () => api.retryPicture(scope, picture.asset_id, picture.version_id),
    onSuccess: (next) => client.setQueryData(key, next),
  })
  // When it becomes ready, the list and the small copies are fetched afresh.
  const last = useRef(state.data?.status)
  useEffect(() => {
    const status = state.data?.status
    if (status && status !== last.current && last.current) {
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'pictures') })
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture', picture.asset_id) })
    }
    last.current = status
  }, [state.data?.status, client, scope, picture.asset_id])

  const data = state.data
  return (
    <div className="stack tight">
      <ErrorNotice error={state.error} prefix="Could not tell how this picture is doing" />
      {data?.status === 'failed' && (
        <div className="notice warn small" role="status">
          <span>
            <strong>No small copy for lists.</strong> {data.message} The file you added is kept as
            it is.
          </span>
          {canWrite && data.retryable && (
            <Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate()}>
              {retry.isPending ? 'Trying again…' : 'Try again'}
            </Button>
          )}
        </div>
      )}
      {(data?.status === 'queued' || data?.status === 'processing') && (
        <p className="muted small" role="status">
          {data.status === 'queued' ? 'Waiting to be prepared.' : 'Being prepared now.'} Until then
          lists show the full picture.
        </p>
      )}
      {data?.status === 'ready' && data.width != null && data.height != null && (
        <p className="muted small">
          {data.width} × {data.height} pixels. A small copy is ready for lists; the picture opens in
          full.
        </p>
      )}
      <ErrorNotice error={retry.error} prefix="Could not try again" />
    </div>
  )
}

function AssetDetail({ asset, picture }: { asset: Asset; picture: LibraryPicture | undefined }) {
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
      {picture && (
        <div className="media-picture">
          {/* A picture that could not be prepared may not open in a browser either. */}
          {picture.processing !== 'failed' && (
            <PictureImage
              assetId={picture.asset_id}
              versionId={picture.version_id}
              label={picture.name}
              size="web"
              className="picture-full"
              openable
            />
          )}
          <PictureState picture={picture} />
          <p>
            <Link to={`/projects/${scope.projectId}/pictures/${picture.asset_id}`}>
              Open the picture’s page
            </Link>{' '}
            <span className="hint">to have it described, review what was seen, and choose how the assistant uses it.</span>
          </p>
        </div>
      )}
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
  // The pictures among them: their small copies and how far each is in being prepared.
  const library = usePictureLibrary(scope)
  const pictures = new Map((library.data?.items ?? []).map((picture) => [picture.asset_id, picture]))

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
          <StorageLine storage={library.data?.storage} />
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
            const picture = pictures.get(asset.id)
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
                  {picture ? (
                    <PictureImage
                      assetId={picture.asset_id}
                      versionId={picture.version_id}
                      label=""
                      className="media-thumb"
                    />
                  ) : (
                    <span className="avatar sm tint-blue" aria-hidden="true">
                      <Icon name="image" size={15} />
                    </span>
                  )}
                  <strong className="grow">{asset.name}</strong>
                  <PreparingBadge picture={picture} />
                  {asset.lifecycle === 'active' ? (
                    <Badge tone="ok">Active</Badge>
                  ) : (
                    <Badge tone="danger">{words(asset.lifecycle)}</Badge>
                  )}
                </button>
                {open && <AssetDetail asset={asset} picture={picture} />}
              </li>
            )
          })}
        </ul>
      </div>
    </>
  )
}
