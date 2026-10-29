import type { Scope } from '../api/client'
import { usePictureUrl } from '../lib/pictures'
import type { Sending } from '../lib/spend'
import { plural } from '../lib/words'

const number = (value: number) => value.toLocaleString('en-GB')

/**
 * What a request will send and where, before it is sent (CTX-013): the provider and the
 * model, how many words of the story go with it, each picture that goes as itself (shown
 * small), and in plain words where it all goes. Shown in the question asked before
 * sending, and beside a draft's cost. Names and counts only; never a key.
 */
export function SendPreview({ sending, scope, compact = false }: { sending: Sending; scope?: Scope; compact?: boolean }) {
  const pictures = sending.pictures
  const model = sending.model ? ` (${sending.model})` : ''
  if (compact) {
    const where =
      sending.where === 'company'
        ? `It goes to ${sending.provider_label}${model}, on a company’s servers outside this site`
        : sending.where === 'your_computer'
          ? `It goes to the model on your own computer${model}, not to any company`
          : `It goes to ${sending.provider_label}${model} on this site’s own network, not to any company`
    return (
      <p className="send-preview-line">
        {where}
        {pictures.length > 0 ? `, with ${plural(pictures.length, 'picture')} from your story.` : '.'}
      </p>
    )
  }
  return (
    <section className="send-preview" aria-label="What will be sent">
      <dl className="send-preview-facts">
        <div>
          <dt>Goes to</dt>
          <dd>
            {sending.provider_label}
            {sending.destination && !sending.provider_label.includes(sending.destination) ? (
              <span className="muted"> ({sending.destination})</span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{sending.model || 'The one chosen for this workspace'}</dd>
        </div>
        <div>
          <dt>From your story</dt>
          <dd>
            About {number(sending.words)} {sending.words === 1 ? 'word' : 'words'}
            {pictures.length > 0 ? ` and ${plural(pictures.length, 'picture')}` : ''}
          </dd>
        </div>
      </dl>
      <p className={sending.where === 'company' ? 'send-preview-where' : 'send-preview-where muted'}>
        It goes {sending.where_words}.
      </p>
      {sending.fits === false && sending.context_tokens ? (
        <p className="notice warn small">
          This model reads at most about {number(sending.context_tokens)} tokens; this is about{' '}
          {number(sending.estimated_tokens)}. The end may be cut off or refused.
        </p>
      ) : null}
      {pictures.length > 0 && (
        <ul className="send-preview-pictures" aria-label="Pictures that will be sent">
          {pictures.map((picture) => (
            <SentPicture key={`${picture.asset_id}:${picture.version_id}`} picture={picture} scope={scope} />
          ))}
        </ul>
      )}
    </section>
  )
}

function SentPicture({
  picture,
  scope,
}: {
  picture: Sending['pictures'][number]
  scope?: Scope
}) {
  const url = usePictureUrl(
    scope ?? { workspaceId: null, projectId: '' },
    scope ? { assetId: picture.asset_id, versionId: picture.version_id } : undefined,
    'thumbnail',
  )
  return (
    <li>
      {url.data ? (
        <img src={url.data} alt="" width={56} height={56} />
      ) : (
        <span className="send-preview-thumb" aria-hidden="true" />
      )}
      <span>{picture.name}</span>
    </li>
  )
}
