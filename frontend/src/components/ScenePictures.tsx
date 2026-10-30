import { useRef, useState, type ChangeEvent } from 'react'
import { api, type AssetLink, type LibraryPicture } from '../api/client'
import { useScenePictures } from '../lib/pictures'
import { useCanonWrite, useProject } from '../project'
import { PictureImage, PictureLibrary } from './Pictures'
import { Button, ErrorNotice, UndoNotice, WriteProblem } from './ui'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/webp'
// How many pictures the head shows before it says how many more there are.
const IN_HEAD = 5

/**
 * The scene's pictures in the studio head, small: each is the picture's small copy once
 * it has been made. The button opens the place to change them.
 */
export function ScenePictureStrip({
  sceneId,
  title,
  open,
  canWrite,
  onToggle,
}: {
  sceneId: string
  title: string
  open: boolean
  canWrite: boolean
  onToggle: () => void
}) {
  const { scope } = useProject()
  const { pictures, names } = useScenePictures(scope, sceneId)
  if (pictures.length === 0 && !canWrite) return null
  const more = pictures.length - IN_HEAD
  return (
    <div className="scene-strip">
      {pictures.length > 0 && (
        <ul className="scene-strip-pictures" aria-label={`Pictures for “${title}”`}>
          {pictures.slice(0, IN_HEAD).map((link) => (
            <li key={link.id}>
              <PictureImage
                assetId={link.asset_id}
                versionId={link.asset_version_id}
                label={names.get(link.asset_id) ?? `A picture for “${title}”`}
                className="scene-thumb"
              />
            </li>
          ))}
          {more > 0 && <li className="hint">and {more} more</li>}
        </ul>
      )}
      {canWrite && (
        <Button size="sm" variant="ghost" icon="image" aria-expanded={open} aria-controls="scene-pictures" onClick={onToggle}>
          {pictures.length > 0 ? 'Change pictures' : 'Add a picture'}
        </Button>
      )}
    </div>
  )
}

/**
 * Where the author chooses the scene's pictures (AST-EXP-018): from the story's picture
 * library, including pictures shown nowhere now, or a new upload. Choosing links the
 * picture to the scene; removing retires the link and keeps the picture and its history.
 * Nothing is read from a picture and none is sent to the assistant.
 */
export function ScenePicturesPanel({ sceneId, title, onClose }: { sceneId: string; title: string; onClose: () => void }) {
  const { scope } = useProject()
  const { pictures, names, library, error } = useScenePictures(scope, sceneId)
  const input = useRef<HTMLInputElement>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [removed, setRemoved] = useState<AssetLink | null>(null)
  const [added, setAdded] = useState<string | null>(null)

  const upload = useCanonWrite<unknown, File>(
    (expectedRevision, file) =>
      api.addScenePicture(scope, sceneId, file, expectedRevision, file.name.replace(/\.[^.]+$/, '')),
    (_result, file) => {
      setRemoved(null)
      setAdded(file.name.replace(/\.[^.]+$/, ''))
    },
  )
  const choose = useCanonWrite<unknown, LibraryPicture>(
    (expectedRevision, picture) => api.showPicture(scope, picture, { type: 'plan_item', id: sceneId }, expectedRevision),
    (_result, picture) => {
      setRemoved(null)
      setAdded(picture.name)
    },
  )
  const remove = useCanonWrite<unknown, AssetLink>(
    (expectedRevision, link) => api.retireLink(scope, link.id, expectedRevision),
    (_result, link) => {
      setAdded(null)
      setRemoved(link)
    },
  )
  // The stored picture is untouched by a removal, so undoing it links it again.
  const restore = useCanonWrite<unknown, AssetLink>(
    (expectedRevision, link) => api.relinkPicture(scope, link, expectedRevision),
    () => setRemoved(null),
  )

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
    void upload.submit(file)
  }

  const shown = new Set(pictures.map((link) => link.asset_version_id ?? ''))
  const nameOf = (link: AssetLink) => names.get(link.asset_id) ?? 'this picture'
  const busy = upload.pending || choose.pending || remove.pending || restore.pending

  return (
    <section id="scene-pictures" className="studio-ask stack" aria-labelledby="scene-pictures-heading">
      <div className="scene-pictures-head">
        <div>
          <h2 id="scene-pictures-heading">Pictures for this scene</h2>
          <p className="muted small">
            Shown at the top of the scene while you write. They are for you: nothing is read from
            them, and they are not sent to the assistant.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>

      <ErrorNotice error={error} prefix="Could not load the scene's pictures" />
      {added && (
        <p className="notice ok small" role="status">
          <span>
            <strong>“{added}”</strong> is now shown on this scene.
          </span>
        </p>
      )}
      {removed && (
        <UndoNotice busy={restore.pending} onUndo={() => void restore.submit(removed)}>
          “{nameOf(removed)}” is no longer shown on this scene. It stays in your picture library.
        </UndoNotice>
      )}

      <div className="stack tight">
        <h3 className="studio-side-title">On this scene</h3>
        {pictures.length === 0 ? (
          <p className="muted small">None yet. Choose one from your library below, or upload one.</p>
        ) : (
          <ul className="pictures">
            {pictures.map((link) => (
              <li key={link.id}>
                <PictureImage assetId={link.asset_id} versionId={link.asset_version_id} label={nameOf(link)} openable />
                <span className="picture-name small">{nameOf(link)}</span>
                <button
                  type="button"
                  className="btn ghost sm picture-remove"
                  aria-label={`Remove “${nameOf(link)}” from “${title}”`}
                  disabled={busy}
                  onClick={() => void remove.submit(link)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="stack tight">
        <div className="scene-pictures-add">
          <h3 className="studio-side-title">From your picture library</h3>
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            aria-label={`Choose a picture for “${title}”`}
            tabIndex={-1}
            onChange={onFile}
          />
          <Button size="sm" icon="image" disabled={busy} onClick={() => input.current?.click()}>
            {upload.pending ? 'Uploading…' : 'Upload a new picture'}
          </Button>
        </div>
        <p className="hint">PNG, JPEG or WebP, up to 8 MB. Pictures on your entries are here too.</p>
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        {library.isPending ? (
          <p className="muted small" role="status">
            Opening your picture library…
          </p>
        ) : (
          <PictureLibrary
            library={library.data?.items ?? []}
            exclude={shown}
            busy={busy}
            actionLabel={(picture) => `Use “${picture.name}” for “${title}”`}
            onChoose={(picture) => void choose.submit(picture)}
          />
        )}
        <ErrorNotice error={library.error} prefix="Could not open your picture library" />
      </div>

      <WriteProblem write={upload} scope={scope} />
      <WriteProblem write={choose} scope={scope} />
      <WriteProblem write={remove} scope={scope} />
      <WriteProblem write={restore} scope={scope} />
    </section>
  )
}
