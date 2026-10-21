import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Asset, type FoundPicture, type LibraryPicture, type PictureStorage } from '../api/client'
import { Icon } from '../components/icons'
import { PictureImage } from '../components/Pictures'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorNotice, Loading, WriteProblem } from '../components/ui'
import { usePictureLibrary } from '../lib/pictures'
import { fullDate, timeAgo } from '../lib/time'
import { words } from '../lib/words'
import { actorLabel, scopedKey, useCanonWrite, useEntries, useInvalidateProject, useProject } from '../project'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/webp'

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
  const { scope, userId, canAdmin } = useProject()
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
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const sceneNames = new Map((outline.data?.items ?? []).map((item) => [item.id, item.title]))
  const shownOn = (type: string, id: string) =>
    entries.names.get(id) ?? sceneNames.get(id) ?? (type === 'plan_item' ? 'A scene no longer in the plan' : 'An entry')

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
                {shownOn(link.target_record_type, link.target_record_id)}
              </span>
              <Badge>{link.role}</Badge>
              {link.retired_revision != null && <Badge tone="danger">Removed</Badge>}
            </li>
          ))}
          {links.data?.items.length === 0 && <li className="muted">Not linked to anything.</li>}
        </ul>
      </div>
      {canAdmin && asset.lifecycle !== 'deleted' && <DeletePictureFile asset={asset} shown={picture?.shown ?? 0} />}
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

type Target = { kind: 'entity' | 'scene'; id: string; name: string }

/**
 * Add a picture from this page (AST-007). Every picture is shown on an entry or a scene,
 * so the library never holds a picture no one placed; the author picks which.
 */
function AddPicture() {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const fileId = useId()
  const targetId = useId()
  const nameId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [added, setAdded] = useState<{ name: string; where: string; assetId: string } | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const people = entries.items
    .filter((item) => item.lifecycle === 'active')
    .sort((a, b) => a.name.localeCompare(b.name))
  const scenes = (outline.data?.items ?? []).filter((item) => item.kind === 'scene')
  const targets: Target[] = [
    ...people.map((item) => ({ kind: 'entity' as const, id: item.id, name: item.name })),
    ...scenes.map((item) => ({ kind: 'scene' as const, id: item.id, name: item.title })),
  ]
  const chosen = targets.find((item) => `${item.kind}:${item.id}` === target) ?? targets[0]

  const add = useCanonWrite<{ link: { asset_id: string } }, { file: File; to: Target; label: string }>(
    (expectedRevision, { file: picture, to, label }) =>
      to.kind === 'entity'
        ? api.addPicture(scope, to.id, picture, expectedRevision, label)
        : api.addScenePicture(scope, to.id, picture, expectedRevision, label),
    (result, { to, label }) => {
      setAdded({ name: label, where: to.name, assetId: result.link.asset_id })
      setFile(null)
      setName('')
      if (input.current) input.current.value = ''
    },
  )

  const onFile = (picked: File | null) => {
    setAdded(null)
    setFile(picked)
    if (!picked) return setProblem(null)
    // Checked here for a quick answer; the server checks the bytes themselves.
    if (picked.size > MAX_BYTES) return setProblem('That picture is larger than 8 MB.')
    if (picked.type && !ACCEPT.split(',').includes(picked.type)) {
      return setProblem('Only PNG, JPEG and WebP pictures can be added.')
    }
    setProblem(null)
    if (!name) setName(picked.name.replace(/\.[^.]+$/, ''))
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!file) return setProblem('Choose a picture file first.')
    if (problem || !chosen) return
    void add.submit({ file, to: chosen, label: name.trim() || file.name.replace(/\.[^.]+$/, '') })
  }

  if (!entries.loading && !outline.isPending && targets.length === 0) {
    return (
      <section className="card" aria-labelledby={`${fileId}-head`}>
        <h2 id={`${fileId}-head`}>Add a picture</h2>
        <p className="muted">
          Every picture is shown on an entry or a scene. Add an entry or a scene first, then add its picture here.
        </p>
      </section>
    )
  }

  return (
    <section className="card media-add" aria-labelledby={`${fileId}-head`}>
      <h2 id={`${fileId}-head`}>Add a picture</h2>
      <form className="media-add-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor={fileId}>Picture file</label>
          <input
            ref={input}
            id={fileId}
            type="file"
            accept={ACCEPT}
            aria-describedby={`${fileId}-hint`}
            onChange={(event) => onFile(event.target.files?.[0] ?? null)}
          />
          <span id={`${fileId}-hint`} className="hint">
            PNG, JPEG or WebP, up to 8 MB. Where and when it was taken, and the camera, are taken out before it is
            kept.
          </span>
        </div>
        <div className="field">
          <label htmlFor={targetId}>Show it on</label>
          <select
            id={targetId}
            value={chosen ? `${chosen.kind}:${chosen.id}` : ''}
            onChange={(event) => setTarget(event.target.value)}
          >
            {people.length > 0 && (
              <optgroup label="Entries">
                {people.map((item) => (
                  <option key={item.id} value={`entity:${item.id}`}>
                    {item.name}
                  </option>
                ))}
              </optgroup>
            )}
            {scenes.length > 0 && (
              <optgroup label="Scenes">
                {scenes.map((item) => (
                  <option key={item.id} value={`scene:${item.id}`}>
                    {item.title}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <div className="field">
          <label htmlFor={nameId}>Name</label>
          <input
            id={nameId}
            type="text"
            maxLength={120}
            value={name}
            aria-describedby={`${nameId}-hint`}
            onChange={(event) => setName(event.target.value)}
          />
          <span id={`${nameId}-hint`} className="hint">
            How it is listed and found. The file’s name is used when this is empty.
          </span>
        </div>
        <div className="actions">
          <Button type="submit" variant="primary" icon="image" disabled={add.pending}>
            {add.pending ? 'Adding…' : 'Add picture'}
          </Button>
        </div>
      </form>
      {problem && (
        <p className="error-text" role="alert">
          {problem}
        </p>
      )}
      <WriteProblem write={add} scope={scope} />
      {added && (
        <p className="notice ok small" role="status">
          <span>
            Added “{added.name}” to {added.where}.{' '}
            <Link to={`/projects/${scope.projectId}/pictures/${added.assetId}`}>Open the picture’s page</Link>
          </span>
        </p>
      )}
    </section>
  )
}

const FOUND_BY: Record<string, string> = {
  name: 'Found by its name',
  description: 'Found by its description',
  picture: 'Found by meaning',
  caption: 'Found by meaning, from its description',
}

/** Find pictures by words: their name and reviewed description, then meaning (AST-007). */
function FindPictures() {
  const { scope } = useProject()
  const fieldId = useId()
  const [typed, setTyped] = useState('')
  const [asked, setAsked] = useState('')
  const [empty, setEmpty] = useState(false)
  const found = useQuery({
    queryKey: scopedKey(scope, 'pictures', 'search', asked),
    queryFn: () => api.searchPictures(scope, asked),
    enabled: asked.length > 0,
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setEmpty(!typed.trim())
    if (typed.trim()) setAsked(typed.trim())
  }
  const items: FoundPicture[] = found.data?.items ?? []
  return (
    <section className="card" aria-labelledby={`${fieldId}-head`}>
      <h2 id={`${fieldId}-head`}>Find pictures</h2>
      <form className="media-find" role="search" onSubmit={submit}>
        <div className="field grow">
          <label htmlFor={fieldId}>Words to look for</label>
          <input
            id={fieldId}
            type="search"
            value={typed}
            maxLength={200}
            placeholder="a harbor at dusk"
            aria-describedby={`${fieldId}-hint`}
            onChange={(event) => setTyped(event.target.value)}
          />
          <span id={`${fieldId}-hint`} className="hint">
            Looks in each picture’s name and in the descriptions you accepted or corrected, then by meaning when
            that is set up.
          </span>
        </div>
        <div className="actions">
          <Button type="submit" icon="search" disabled={found.isFetching}>
            {found.isFetching ? 'Looking…' : 'Find'}
          </Button>
        </div>
      </form>
      {empty && (
        <p className="error-text" role="alert">
          Type a word or two to look for.
        </p>
      )}
      <ErrorNotice error={found.error} prefix="Could not look for pictures" />
      {asked && found.isSuccess && (
        <div className="stack tight" aria-live="polite">
          {items.length === 0 ? (
            <p className="muted">No picture matches “{asked}”.</p>
          ) : (
            <ul className="media-found">
              {items.map((item) => (
                <li key={item.asset_id} className="row">
                  <PictureImage assetId={item.asset_id} versionId={item.version_id} label="" className="media-thumb" />
                  <span className="grow">
                    <Link to={`/projects/${scope.projectId}/pictures/${item.asset_id}`}>{item.name}</Link>
                    <span className="hint block">{FOUND_BY[item.matched] ?? 'Found by meaning'}</span>
                  </span>
                  {item.shown === 0 && <Badge>Not shown anywhere</Badge>}
                </li>
              ))}
            </ul>
          )}
          {!found.data.by_meaning && (
            <p className="hint">Search by meaning is not available here, so only names and descriptions were searched.</p>
          )}
        </div>
      )}
    </section>
  )
}

/**
 * Delete a picture's files (AST-008): the one thing here that cannot be undone, so it is
 * kept apart, worded plainly, asks first, and needs a deliberate tick (D1, D5, D6).
 */
function DeletePictureFile({ asset, shown }: { asset: Asset; shown: number }) {
  const { scope } = useProject()
  const invalidate = useInvalidateProject(scope)
  const [asking, setAsking] = useState(false)
  const [understood, setUnderstood] = useState(false)
  const tickId = useId()
  const remove = useMutation({
    mutationFn: () => api.deletePicture(scope, asset.id),
    onSuccess: async () => {
      setAsking(false)
      await invalidate()
    },
  })
  return (
    <div className="danger-zone">
      <div>
        <h2>Delete this picture’s file</h2>
        {shown > 0 ? (
          <p className="muted small">
            It is shown on {shown === 1 ? 'one entry or scene' : `${shown} entries or scenes`}. Take it off them
            first; then its file can be deleted.
          </p>
        ) : (
          <p className="muted small">
            Removes the file, its smaller copies and what search had of it, for good. Its record stays, so history
            still says a picture was here.
          </p>
        )}
      </div>
      <Button
        variant="danger"
        disabled={shown > 0}
        onClick={() => {
          setUnderstood(false)
          setAsking(true)
        }}
      >
        Delete the file
      </Button>
      {asking && (
        <ConfirmDialog
          title={`Delete the file of “${asset.name}”?`}
          confirmLabel={remove.isPending ? 'Deleting…' : 'Delete the file for good'}
          keepLabel="Keep the picture"
          busy={!understood || remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate()}
          onCancel={() => setAsking(false)}
        >
          <p>
            The picture file, its smaller copies, and what search had of it are removed. This cannot be undone: an
            archive you saved earlier is the only way to have it back.
          </p>
          <label htmlFor={tickId} className="check">
            <input id={tickId} type="checkbox" checked={understood} onChange={(event) => setUnderstood(event.target.checked)} />
            I understand the file cannot be brought back.
          </label>
        </ConfirmDialog>
      )}
    </div>
  )
}

/** The story's pictures and documents: add, find, open, and what each is linked to. */
export function MediaPage() {
  const { scope, canWrite } = useProject()
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
            The pictures and documents in this story, and the entries and scenes they are shown on. Add a picture here
            or from an entry, and find one by its name or what it shows.
          </p>
          <StorageLine storage={library.data?.storage} />
        </div>
      </div>
      <div className="media-tools">
        {canWrite && <AddPicture />}
        <FindPictures />
      </div>
      <div className="section-gap">
        <h2 className="media-list-head">Everything in the story</h2>
        <ErrorNotice error={assets.error} prefix="Could not load media" />
        {assets.isPending && <Loading>Loading media…</Loading>}
        {assets.isSuccess && items.length === 0 && (
          <EmptyState icon="image" title="No media yet">
            Nothing has been added to this story yet. Add a picture above, or open an entry and choose “Add picture”.
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
