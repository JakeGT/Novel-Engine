import { Link } from 'react-router-dom'
import type { AssistantNote } from '../api/client'
import { usePictureUrl } from '../lib/pictures'
import { boxStyle } from '../lib/regions'
import { useProject } from '../project'
import '../styles/picture.css'

/**
 * The picture a note of the pictures check was compared with (CON-014), small, with the
 * part it is about drawn on it, and a way to open the picture.
 */
export function NotePicture({ picture }: { picture: NonNullable<AssistantNote['picture']> }) {
  const { scope } = useProject()
  const url = usePictureUrl(scope, { assetId: picture.asset_id, versionId: picture.version_id }, 'web')
  const region = picture.region && picture.region.length === 4 ? picture.region : null
  const name = picture.label.replace(/^“|”(, version \d+)?$/g, '') || 'the picture'
  const where = picture.part_label
    ? `The part “${picture.part_label}” is marked.`
    : region
      ? 'Where the note is about is marked.'
      : 'About the whole picture.'
  return (
    <figure className="note-picture">
      <div className="note-picture-frame">
        {url.data ? (
          <div className="picture-mat-inner">
            <img src={url.data} alt={`${name}: ${where}`} />
            {region && (
              <span
                className="picture-mark"
                aria-hidden="true"
                style={boxStyle({ x: region[0], y: region[1], width: region[2], height: region[3] })}
              />
            )}
          </div>
        ) : url.isError ? (
          <span className="small muted">The picture could not be loaded.</span>
        ) : (
          <span className="picture loading" role="status" aria-label="Loading picture" />
        )}
      </div>
      <figcaption className="small muted">
        {where}{' '}
        <Link to={`/projects/${scope.projectId}/pictures/${picture.asset_id}`}>Open the picture</Link>
      </figcaption>
    </figure>
  )
}
