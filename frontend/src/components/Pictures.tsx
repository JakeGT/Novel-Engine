import { useQuery } from '@tanstack/react-query'
import { useRef, useState, type ChangeEvent } from 'react'
import {
  api,
  type AssetLink,
  type CanonEntity,
  type LibraryPicture,
  type PictureSize,
  type Scope,
} from '../api/client'
import { isPicture, usePictureLibrary, usePictureUrl } from '../lib/pictures'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { Badge, Button, Dialog, ErrorNotice, TypeAvatar, UndoNotice, WriteProblem } from './ui'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/webp'

const ofLink = (link: AssetLink | undefined) =>
  link ? { assetId: link.asset_id, versionId: link.asset_version_id } : undefined

/** An entry's seal: its picture when it has one, otherwise its initial. */
export function Portrait({
  entity,
  link,
  size,
}: {
  entity: Pick<CanonEntity, 'type' | 'name'>
  link?: AssetLink
  size?: 'sm' | 'lg'
}) {
  const { scope } = useProject()
  const picture = usePictureUrl(scope, ofLink(link), 'thumbnail')
  if (!link || !picture.data) return <TypeAvatar type={entity.type} name={entity.name} size={size} />
  return (
    <span className={`avatar portrait ${size ?? ''}`}>
      <img src={picture.data} alt={`Picture of ${entity.name}`} />
    </span>
  )
}

/**
 * A stored picture. Lists show its small copy, made on the server once the picture has
 * been prepared (the full picture until then); ``openable`` lets the author open the
 * full picture over the page.
 */
export function PictureImage({
  assetId,
  versionId,
  label,
  className = 'picture',
  size = 'thumbnail',
  openable = false,
}: {
  assetId: string
  versionId: string | null
  label: string
  className?: string
  size?: PictureSize
  openable?: boolean
}) {
  const { scope } = useProject()
  const picture = usePictureUrl(scope, { assetId, versionId }, size)
  const [open, setOpen] = useState(false)
  if (picture.isError) return <span className={`${className} missing`}>Could not load</span>
  if (!picture.data) return <span className={`${className} loading`} role="status" aria-label="Loading picture" />
  const image = <img className={className} src={picture.data} alt={label} decoding="async" />
  if (!openable) return image
  return (
    <>
      <button type="button" className="picture-open" aria-label={`Open ${label}`} onClick={() => setOpen(true)}>
        {image}
      </button>
      {open && <PictureViewer assetId={assetId} versionId={versionId} label={label} onClose={() => setOpen(false)} />}
    </>
  )
}

/** The full picture, opened over the page; closing returns to where the author was. */
export function PictureViewer({
  assetId,
  versionId,
  label,
  onClose,
}: {
  assetId: string
  versionId: string | null
  label: string
  onClose: () => void
}) {
  return (
    <Dialog title={label} onClose={onClose} wide>
      <div className="picture-viewer">
        <PictureImage assetId={assetId} versionId={versionId} label={label} size="original" className="picture-full" />
      </div>
    </Dialog>
  )
}

function Thumbnail({ link, label }: { link: AssetLink; label: string }) {
  return <PictureImage assetId={link.asset_id} versionId={link.asset_version_id} label={label} openable />
}

/**
 * The story's pictures that can be chosen here: every stored picture, including those
 * shown nowhere now, except the ones already in place.
 */
export function PictureLibrary({
  library,
  exclude,
  busy,
  actionLabel,
  onChoose,
}: {
  library: LibraryPicture[]
  exclude: Set<string>
  busy: boolean
  actionLabel: (picture: LibraryPicture) => string
  onChoose: (picture: LibraryPicture) => void
}) {
  const choices = library.filter((picture) => !exclude.has(picture.version_id))
  if (choices.length === 0) {
    return (
      <p className="muted small">
        {library.length === 0 ? 'No other pictures in your story yet.' : 'Every picture in your story is already here.'}
      </p>
    )
  }
  return (
    <ul className="pictures">
      {choices.map((picture) => (
        <li key={picture.asset_id}>
          <PictureImage assetId={picture.asset_id} versionId={picture.version_id} label={picture.name} openable />
          <span className="picture-name small">{picture.name}</span>
          {picture.shown === 0 && <span className="hint">Not shown anywhere</span>}
          <button
            type="button"
            className="btn ghost sm"
            aria-label={actionLabel(picture)}
            disabled={busy}
            onClick={() => onChoose(picture)}
          >
            Use this
          </button>
        </li>
      ))}
    </ul>
  )
}

/** The pictures linked to an entry, with a way to add and remove them. */
export function PicturesPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const input = useRef<HTMLInputElement>(null)
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', entity.id),
    queryFn: () => api.listLinks(scope, { target_id: entity.id, include_retired: false }),
  })
  const items = links.data?.items ?? []
  const pictures = items.filter(isPicture).sort((a, b) => b.created_revision - a.created_revision)
  const others = items.filter((link) => !isPicture(link))
  const active = entity.lifecycle === 'active'

  const add = useCanonWrite<unknown, File>((expectedRevision, file) =>
    api.addPicture(scope, entity.id, file, expectedRevision, file.name.replace(/\.[^.]+$/, '')),
  )
  const [removed, setRemoved] = useState<AssetLink | null>(null)
  const remove = useCanonWrite<unknown, AssetLink>(
    (expectedRevision, link) => api.retireLink(scope, link.id, expectedRevision),
    (_result, link) => setRemoved(link),
  )
  // The stored picture is untouched by a removal, so it can simply be linked again.
  const restore = useCanonWrite<unknown, AssetLink>(
    (expectedRevision, link) => api.relinkPicture(scope, link, expectedRevision),
    () => setRemoved(null),
  )

  // The newest picture is the main one, so making another the main one links it afresh
  // and retires its older link: two small versions, and nothing is lost if the second fails.
  const promote = useCanonWrite<unknown, AssetLink>(async (expectedRevision, link) => {
    await api.relinkPicture(scope, link, expectedRevision)
    return api.retireLink(scope, link.id, expectedRevision + 1)
  })
  // Every picture in the story, including those shown nowhere now, that this entry does not have.
  const everywhere = usePictureLibrary(scope, canWrite && active)
  const mine = new Set(pictures.map((link) => link.asset_version_id ?? ''))
  const reuse = useCanonWrite<unknown, LibraryPicture>((expectedRevision, picture) =>
    api.showPicture(scope, picture, { type: 'entity', id: entity.id }, expectedRevision),
  )

  const [problem, setProblem] = useState<string | null>(null)
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // Let the same file be chosen again after a failure.
    event.target.value = ''
    if (!file) return
    // Checked here for a quick answer; the server checks the bytes themselves.
    if (file.size > MAX_BYTES) return setProblem('That picture is larger than 8 MB.')
    if (file.type && !ACCEPT.split(',').includes(file.type)) {
      return setProblem('Only PNG, JPEG and WebP pictures can be added.')
    }
    setProblem(null)
    void add.submit(file)
  }

  if (pictures.length === 0 && others.length === 0 && !(canWrite && active)) return null

  return (
    <section className="card" aria-labelledby="pictures-heading">
      <div className="card-head">
        <h2 id="pictures-heading">Pictures</h2>
        {canWrite && active && (
          <>
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              aria-label={`Choose a picture of ${entity.name}`}
              tabIndex={-1}
              onChange={onFile}
            />
            <Button size="sm" icon="image" disabled={add.pending} onClick={() => input.current?.click()}>
              {add.pending ? 'Adding…' : 'Add picture'}
            </Button>
          </>
        )}
      </div>
      <ErrorNotice error={links.error} prefix="Could not load pictures" />
      {pictures.length === 0 && others.length === 0 && (
        <p className="muted">
          No pictures yet. Add a portrait, a sketch, or a reference — PNG, JPEG or WebP, up to 8 MB.
        </p>
      )}
      {problem && (
        <p className="error-text" role="alert">
          {problem}
        </p>
      )}
      {removed && (
        <UndoNotice busy={restore.pending} onUndo={() => void restore.submit(removed)}>
          Picture removed from {entity.name}. It stays in the history.
        </UndoNotice>
      )}
      <WriteProblem write={restore} scope={scope} />
      <ul className="pictures">
        {pictures.map((link, index) => (
          <li key={link.id}>
            <Thumbnail link={link} label={`Picture ${index + 1} of ${entity.name}`} />
            {index === 0 && pictures.length > 1 && <Badge tone="accent">Main</Badge>}
            {canWrite && active && index > 0 && (
              <button
                type="button"
                className="btn ghost sm"
                aria-label={`Make picture ${index + 1} the main picture of ${entity.name}`}
                disabled={promote.pending}
                onClick={() => void promote.submit(link)}
              >
                Make main
              </button>
            )}
            {canWrite && active && (
              <button
                type="button"
                className="btn ghost sm picture-remove"
                aria-label={`Remove picture ${index + 1} of ${entity.name}`}
                disabled={remove.pending}
                onClick={() => void remove.submit(link)}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {others.length > 0 && (
        <ul className="rows">
          {others.map((link) => (
            <li className="row" key={link.id}>
              <span className="grow">Linked media</span>
              <Badge>{link.role.replace(/_/g, ' ').toLowerCase()}</Badge>
            </li>
          ))}
        </ul>
      )}
      {canWrite && active && (everywhere.data?.items ?? []).some((picture) => !mine.has(picture.version_id)) && (
        <details className="more">
          <summary>Use a picture already in your story</summary>
          <PictureLibrary
            library={everywhere.data?.items ?? []}
            exclude={mine}
            busy={reuse.pending}
            actionLabel={(picture) => `Use “${picture.name}” for ${entity.name}`}
            onChoose={(picture) => void reuse.submit(picture)}
          />
        </details>
      )}
      <WriteProblem write={add} scope={scope} />
      <WriteProblem write={remove} scope={scope} />
      <WriteProblem write={promote} scope={scope} />
      <WriteProblem write={reuse} scope={scope} />
    </section>
  )
}
