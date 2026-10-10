import { useQuery } from '@tanstack/react-query'
import { useRef, useState, type ChangeEvent } from 'react'
import { api, type AssetLink, type CanonEntity, type Scope } from '../api/client'
import { isPicture } from '../lib/pictures'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { Badge, Button, ErrorNotice, TypeAvatar, UndoNotice, WriteProblem } from './ui'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/webp'

function usePictureUrl(scope: Scope, link: AssetLink | undefined) {
  return useQuery({
    queryKey: scopedKey(scope, 'picture', link?.asset_id, link?.asset_version_id),
    queryFn: () => api.pictureUrl(scope, link!.asset_id, link!.asset_version_id as string),
    enabled: Boolean(link?.asset_version_id),
    // A stored version never changes.
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

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
  const picture = usePictureUrl(scope, link)
  if (!link || !picture.data) return <TypeAvatar type={entity.type} name={entity.name} size={size} />
  return (
    <span className={`avatar portrait ${size ?? ''}`}>
      <img src={picture.data} alt={`Picture of ${entity.name}`} />
    </span>
  )
}

function Thumbnail({ link, label }: { link: AssetLink; label: string }) {
  const { scope } = useProject()
  const picture = usePictureUrl(scope, link)
  if (picture.isError) return <span className="picture missing">Could not load</span>
  if (!picture.data) return <span className="picture loading" role="status" aria-label="Loading picture" />
  return <img className="picture" src={picture.data} alt={label} />
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
      <WriteProblem write={add} scope={scope} />
      <WriteProblem write={remove} scope={scope} />
    </section>
  )
}
