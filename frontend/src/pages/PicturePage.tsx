import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  pictureApi,
  type Picture,
  type PictureChoice,
  type PictureLink,
  type PictureObservation,
  uploadApi,
  type WorldTime,
} from '../api/client'
import { JobProgress } from '../components/StudioWork'
import { Badge, Button, ErrorNotice, Loading } from '../components/ui'
import { WhenField } from '../components/WhenField'
import { usePictureUrl } from '../lib/pictures'
import { useStoryClock } from '../lib/storytime'
import { isFinished, tokenCount } from '../lib/studio'
import { plural } from '../lib/words'
import { scopedKey, useEntries, useProject } from '../project'
import '../styles/picture.css'

const JOB_EVERY = 1500

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const TYPE_WORDS: Record<string, string> = {
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/webp': 'WebP',
}

/** How sure the assistant said it was, in words first. */
function sureness(confidence: number | null | undefined): string | null {
  if (confidence == null) return null
  const percent = Math.round(confidence * 100)
  const word = confidence >= 0.8 ? 'Sure' : confidence >= 0.5 ? 'Fairly sure' : 'Unsure'
  return `${word} (${percent}%)`
}

const STATE_WORDS: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' }> = {
  proposed: { label: 'Waiting for you', tone: 'warn' },
  accepted: { label: 'Accepted', tone: 'ok' },
  corrected: { label: 'Corrected by you', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'danger' },
  superseded: { label: 'Replaced by a newer description', tone: 'neutral' },
}

const CHOICES: { value: PictureChoice; label: string; hint: string }[] = [
  {
    value: 'send',
    label: 'Send the picture',
    hint:
      'The picture itself goes to an assistant that can see pictures. One that cannot is given ' +
      'the description you accepted instead, and what was given is recorded with the draft or check.',
  },
  {
    value: 'describe',
    label: 'Use its description',
    hint: 'Only the description you accepted is given; the picture is never sent.',
  },
  {
    value: 'leave_out',
    label: 'Leave out',
    hint: 'Not given to the assistant at all.',
  },
]

/** The picture, with the part one note is about drawn on it. */
function PictureMat({ picture, marked }: { picture: Picture; marked: PictureObservation | null }) {
  const { scope } = useProject()
  const url = usePictureUrl(scope, { assetId: picture.asset_id, versionId: picture.version.id }, 'web')
  const region = marked?.region
  return (
    <figure className="picture-mat">
      <div className="picture-mat-frame">
        {url.isError && <p className="muted">The picture could not be loaded.</p>}
        {!url.data && !url.isError && <span className="picture loading" role="status" aria-label="Loading picture" />}
        {url.data && (
          <div className="picture-mat-inner">
            <img src={url.data} alt={picture.name} />
            {region && (
          <span
            className="picture-mark"
            aria-hidden="true"
            style={{
              left: `${region.x * 100}%`,
              top: `${region.y * 100}%`,
              width: `${region.width * 100}%`,
              height: `${region.height * 100}%`,
            }}
          />
            )}
          </div>
        )}
      </div>
      <figcaption className="small muted">
        {region ? `Marked: where “${marked?.words}” shows.` : 'Version ' + picture.version.version + ' of this picture.'}
      </figcaption>
    </figure>
  )
}

function linkTarget(projectId: string, link: PictureLink): string {
  return link.target_record_type === 'plan_item'
    ? `/projects/${projectId}/plan/${link.target_record_id}/write`
    : `/projects/${projectId}/entries/${link.target_record_id}`
}

const asTime = (value: unknown): WorldTime | null =>
  value && typeof value === 'object' && 'kind' in value ? (value as WorldTime) : null
const placed = (value: WorldTime | null): WorldTime | null => (value && value.kind !== 'unknown' ? value : null)

/** Who may know what the picture shows here, for which story time, and which version. */
function LinkChange({ picture, link, onDone }: { picture: Picture; link: PictureLink; onDone: () => void }) {
  const { scope } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const clock = useStoryClock(scope)
  const [secret, setSecret] = useState(link.secret)
  const [knownBy, setKnownBy] = useState<string[]>(link.known_by)
  // What is set now, to change (AST-001).
  const [from, setFrom] = useState<WorldTime | null>(asTime(link.applies_from_time))
  const [until, setUntil] = useState<WorldTime | null>(asTime(link.applies_until_time))
  const [versionId, setVersionId] = useState<string>(link.shows_version_id ?? '')
  const characters = entries.items.filter((entry) => entry.type === 'Character' && entry.lifecycle !== 'retired')
  const save = useMutation({
    mutationFn: () =>
      pictureApi.changeLink(scope, picture.asset_id, link.id, {
        secret,
        known_by: secret ? knownBy : [],
        applies_from: placed(from),
        applies_until: placed(until),
        version_id: versionId || null,
        expected_revision: picture.revision,
      }),
    onSuccess: (next) => {
      client.setQueryData(scopedKey(scope, 'picture-page', picture.asset_id), next)
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'links') })
      onDone()
    },
  })
  const legend = useId()
  return (
    <form
      className="picture-link-change stack tight"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <label className="check">
        <input type="checkbox" checked={secret} onChange={(event) => setSecret(event.target.checked)} />
        Keep what it shows secret
      </label>
      {secret && (
        <fieldset className="stack tight" aria-describedby={legend}>
          <legend>Who knows what it shows</legend>
          <p id={legend} className="hint">
            The assistant is given this picture only for a scene seen by one of them. With no one
            ticked, it is the author’s alone.
          </p>
          {characters.map((character) => (
            <label key={character.id} className="check">
              <input
                type="checkbox"
                checked={knownBy.includes(character.id)}
                onChange={(event) =>
                  setKnownBy((current) =>
                    event.target.checked ? [...current, character.id] : current.filter((item) => item !== character.id),
                  )
                }
              />
              {character.name}
            </label>
          ))}
        </fieldset>
      )}
      <WhenField
        legend="Shows them from this story time on"
        value={from}
        onChange={setFrom}
        unit={clock.unit}
        events={clock.events}
      />
      <WhenField
        legend="No longer shows them from this story time on"
        value={until}
        onChange={setUntil}
        unit={clock.unit}
        events={clock.events}
      />
      <p className="hint">
        Leave both times as “Not said” for a picture that is true throughout the story. A scene set
        outside these times is not given the picture.
      </p>
      {picture.versions.length > 1 && (
        <label className="stack tight">
          <span>Which version it shows</span>
          <select value={versionId} onChange={(event) => setVersionId(event.target.value)}>
            <option value="">The newest (version {picture.version.version}), as it changes</option>
            {picture.versions.map((item) => (
              <option key={item.id} value={item.id}>
                Always version {item.version}
              </option>
            ))}
          </select>
        </label>
      )}
      <ErrorNotice error={save.error} prefix="Could not change this" />
      <div className="picture-actions">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button onClick={onDone}>Cancel</Button>
      </div>
    </form>
  )
}

function WhereShown({ picture }: { picture: Picture }) {
  const { scope, canWrite } = useProject()
  const [changing, setChanging] = useState<string | null>(null)
  return (
    <section className="picture-section" aria-labelledby="picture-shown">
      <h2 id="picture-shown">Where it is shown</h2>
      {picture.links.length === 0 ? (
        <p className="muted">It is not shown on any entry or scene now.</p>
      ) : (
        <ul className="rows">
          {picture.links.map((link) => (
            <li key={link.id} className="picture-link">
              <div className="picture-actions">
                <span className="grow">
                  <Link to={linkTarget(scope.projectId, link)}>{link.target_name}</Link>{' '}
                  <span className="muted small">
                    {link.target_kind} · {link.role_words}
                  </span>
                </span>
                {canWrite && changing !== link.id && (
                  <Button size="sm" onClick={() => setChanging(link.id)}>
                    Change who knows, when and which version
                  </Button>
                )}
              </div>
              <p className="small muted">
                {link.secret
                  ? link.known_by_names.length
                    ? `Secret: only ${link.known_by_names.join(', ')} ${link.known_by_names.length === 1 ? 'knows' : 'know'} what it shows.`
                    : 'Secret: for the author alone.'
                  : 'Anyone in the story may know what it shows.'}{' '}
                {link.window ? `It shows them ${link.window}.` : 'It holds throughout the story.'}{' '}
                {link.shows_version ? `It always shows version ${link.shows_version}.` : ''}
              </p>
              {changing === link.id && <LinkChange picture={picture} link={link} onDone={() => setChanging(null)} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** A new version of the picture, sent in two checked steps (AST-002). */
function NewVersion({ picture }: { picture: Picture }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [done, setDone] = useState<number | null>(null)
  const add = useMutation({
    mutationFn: (file: File) => uploadApi.upload(scope, { for: 'picture', id: picture.asset_id }, file),
    onSuccess: (made) => {
      setDone(Number(made.version.version))
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture-page', picture.asset_id) })
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'pictures') })
    },
  })
  if (!canWrite) return null
  return (
    <section className="picture-section" aria-labelledby="picture-new-version">
      <h2 id="picture-new-version">Add a new version</h2>
      <p className="hint">
        PNG, JPEG or WebP, up to 8 MB. Where it is shown stays the same: places that show the newest
        version show this one; a place set to always show one version keeps it.
      </p>
      <input
        ref={input}
        className="sr-only"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        aria-label={`Choose a new version of “${picture.name}”`}
        tabIndex={-1}
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) {
            setDone(null)
            add.mutate(file)
          }
        }}
      />
      <div className="picture-actions">
        <Button icon="image" disabled={add.isPending} onClick={() => input.current?.click()}>
          {add.isPending ? 'Adding the new version…' : 'Add a new version'}
        </Button>
      </div>
      <ErrorNotice error={add.error} prefix="Could not add the new version" />
      {done !== null && (
        <p className="small" role="status">
          Added as version {done}. It was checked against what was sent before it was kept.
        </p>
      )}
    </section>
  )
}

function ContextChoice({ picture }: { picture: Picture }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const name = useId()
  // Shown at once; put back if saving fails.
  const [asked, setAsked] = useState<PictureChoice | null>(null)
  const shown = asked ?? picture.context_choice
  const choose = useMutation({
    mutationFn: (choice: PictureChoice) => pictureApi.setChoice(scope, picture.asset_id, choice),
    onMutate: (choice) => setAsked(choice),
    onSettled: () => setAsked(null),
    onSuccess: (next) => {
      client.setQueryData<Picture>(scopedKey(scope, 'picture-page', picture.asset_id), (current) =>
        current ? { ...current, context_choice: next.choice, context_choice_words: next.words } : current,
      )
      // Every scene's context reads the choice.
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'scene-context') })
    },
  })
  return (
    <section className="picture-section" aria-labelledby="picture-context">
      <h2 id="picture-context">Use in the assistant’s context</h2>
      <p className="hint">For drafting and checking the scenes it is shown on and the scenes its people are in.</p>
      <fieldset className="picture-choices" disabled={!canWrite}>
        <legend className="sr-only">How the assistant is given this picture</legend>
        {CHOICES.map((choice) => (
          <label key={choice.value} className={`picture-choice ${shown === choice.value ? 'chosen' : ''}`}>
            <input
              type="radio"
              name={name}
              value={choice.value}
              checked={shown === choice.value}
              onChange={() => !choose.isPending && choose.mutate(choice.value)}
            />
            <span>
              <strong>{choice.label}</strong>
              <span className="hint">{choice.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {choose.isSuccess && (
        <p className="small" role="status">
          Saved: {picture.context_choice_words.toLowerCase()}.
        </p>
      )}
      {picture.context_choice === 'send' && !picture.description && (
        <p className="notice warn small">
          It has no accepted description yet, so an assistant that cannot see pictures is told only
          that it exists.
        </p>
      )}
      <ErrorNotice error={choose.error} prefix="Could not save the choice" />
    </section>
  )
}

function Observation({
  picture,
  observation,
  marked,
  onMark,
}: {
  picture: Picture
  observation: PictureObservation
  marked: boolean
  onMark: (observation: PictureObservation | null) => void
}) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const [correcting, setCorrecting] = useState(false)
  const [words, setWords] = useState(observation.words)
  const [about, setAbout] = useState(observation.subject_record_id ?? '')
  const pageKey = scopedKey(scope, 'picture-page', picture.asset_id)
  const refresh = () => void client.invalidateQueries({ queryKey: pageKey })
  const review = useMutation({
    mutationFn: (decision: 'accept' | 'correct' | 'reject') =>
      pictureApi.review(scope, observation.id, decision, decision === 'correct' ? words : undefined),
    onSuccess: () => {
      setCorrecting(false)
      refresh()
    },
  })
  const propose = useMutation({
    mutationFn: () => pictureApi.propose(scope, observation.id, about || undefined),
    onSuccess: refresh,
  })
  const state = STATE_WORDS[observation.state] ?? { label: observation.state, tone: 'neutral' as const }
  const decided = observation.state === 'accepted' || observation.state === 'corrected'
  const people = entries.items.filter((entry) => ['Character', 'Location', 'Object'].includes(entry.type))
  const fieldId = useId()
  return (
    <li className={`picture-note ${marked ? 'marked' : ''}`}>
      <div className="picture-actions">
        <span className="picture-note-kind small">{observation.kind === 'caption' ? 'What it shows' : 'How it looks'}</span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      {correcting ? (
        <div className="field">
          <label htmlFor={fieldId}>Your words</label>
          <textarea id={fieldId} rows={2} value={words} maxLength={600} onChange={(event) => setWords(event.target.value)} />
        </div>
      ) : (
        <p className="picture-note-words">{observation.words}</p>
      )}
      {observation.state === 'corrected' && (
        <p className="small muted">The assistant said: “{observation.text}”</p>
      )}
      <p className="small muted">
        {[
          observation.subject_name ? `About ${observation.subject_name}` : null,
          sureness(observation.confidence),
          observation.region ? 'about one part of the picture' : null,
          observation.model ? `seen by ${observation.model}` : null,
        ]
          .filter(Boolean)
          .join(' · ')}
      </p>
      <div className="picture-actions">
        {observation.region && (
          <Button size="sm" variant="ghost" aria-pressed={marked} onClick={() => onMark(marked ? null : observation)}>
            {marked ? 'Hide where' : 'Show where'}
          </Button>
        )}
        {canWrite && correcting && (
          <>
            <Button size="sm" variant="primary" disabled={review.isPending || !words.trim()} onClick={() => review.mutate('correct')}>
              Save correction
            </Button>
            <Button size="sm" onClick={() => setCorrecting(false)}>
              Cancel
            </Button>
          </>
        )}
        {canWrite && !correcting && observation.state === 'proposed' && (
          <>
            <Button size="sm" variant="primary" disabled={review.isPending} onClick={() => review.mutate('accept')}>
              Accept
            </Button>
            <Button size="sm" disabled={review.isPending} onClick={() => setCorrecting(true)}>
              Correct
            </Button>
            <Button size="sm" variant="danger-quiet" disabled={review.isPending} onClick={() => review.mutate('reject')}>
              Reject
            </Button>
          </>
        )}
        {canWrite && !correcting && decided && !observation.proposal_mutation_set_id && (
          <Button size="sm" disabled={review.isPending} onClick={() => setCorrecting(true)}>
            Correct
          </Button>
        )}
      </div>
      {canWrite && decided && observation.kind === 'appearance' && !observation.proposal_mutation_set_id && (
        <div className="picture-note-fact stack tight">
          {!observation.subject_record_id && (
            <div className="field">
              <label htmlFor={`${fieldId}-about`}>Who or what it is about</label>
              <select id={`${fieldId}-about`} value={about} onChange={(event) => setAbout(event.target.value)}>
                <option value="">Choose…</option>
                {people.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="picture-actions">
            <Button size="sm" disabled={propose.isPending || (!observation.subject_record_id && !about)} onClick={() => propose.mutate()}>
              {propose.isPending
                ? 'Sending…'
                : `Suggest as a detail of ${observation.subject_name ?? entries.names.get(about) ?? 'an entry'}`}
            </Button>
            <span className="hint">It goes to the review queue; nothing in the story changes until you approve it.</span>
          </div>
        </div>
      )}
      {observation.proposal_mutation_set_id && (
        <p className="small">
          Suggested as a detail.{' '}
          <Link to={`/projects/${scope.projectId}/review/${observation.proposal_mutation_set_id}`}>
            Open it in the review queue
          </Link>
        </p>
      )}
      <ErrorNotice error={review.error ?? propose.error} prefix="Could not save that" />
    </li>
  )
}

function Describe({ picture, marked, onMark }: { picture: Picture; marked: PictureObservation | null; onMark: (o: PictureObservation | null) => void }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'picture-describe-estimate', picture.asset_id, picture.version.id),
    queryFn: () => pictureApi.describeEstimate(scope, picture.asset_id),
    enabled: canWrite,
  })
  const job = useQuery({
    queryKey: scopedKey(scope, 'picture-describe-job', jobId),
    queryFn: () => api.job(scope, jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const done = isFinished(job.data)
  useEffect(() => {
    if (done) void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture-page', picture.asset_id) })
  }, [done, client, scope, picture.asset_id])
  const start = useMutation({
    mutationFn: () => pictureApi.describe(scope, picture.asset_id),
    onSuccess: (started) => setJobId(started.id),
  })
  const stop = useMutation({
    mutationFn: () => api.cancelJob(scope, jobId as string),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture-describe-job', jobId) }),
  })
  const current = picture.observations.filter((item) => item.current && !['rejected', 'superseded'].includes(item.state))
  const earlier = picture.observations.filter((item) => !current.includes(item))
  const waiting = current.filter((item) => item.state === 'proposed').length
  const running = Boolean(job.data && !isFinished(job.data))
  const cost = estimate.data
  return (
    <section className="picture-section" aria-labelledby="picture-seen">
      <h2 id="picture-seen">What the assistant saw</h2>
      <p className="hint">
        A description is only a suggestion: accept, correct or reject each part. What you accept is
        what an assistant that cannot see pictures is given instead.
      </p>
      {canWrite && !running && (
        <div className="picture-describe">
          {cost && cost.sees_pictures && (
            <>
              <Button variant="primary" disabled={start.isPending} onClick={() => start.mutate()}>
                {start.isPending ? 'Starting…' : current.length ? 'Describe this picture again' : 'Describe this picture'}
              </Button>
              <span className="hint">
                About {tokenCount(cost.total_tokens)} tokens. The picture is sent to the assistant you use.
              </span>
            </>
          )}
          {cost && !cost.sees_pictures && (
            <p className="notice plain small">
              The assistant you use cannot see pictures, so it cannot describe this one. Nothing is
              sent. You can still say how the assistant uses the picture.
            </p>
          )}
          {estimate.isPending && <Loading>Working out what it would use…</Loading>}
          <ErrorNotice error={estimate.error ?? start.error} prefix="Could not describe the picture" />
        </div>
      )}
      {job.data && running && (
        <JobProgress job={job.data} stopping={stop.isPending} error={stop.error} onStop={() => stop.mutate()} />
      )}
      {job.data?.status === 'failed' && (
        <p className="notice warn small" role="alert">
          The description did not finish: {job.data.error_message ?? 'something went wrong.'}
        </p>
      )}
      {job.data?.status === 'succeeded' && (
        <p className="small" role="status">
          Described. {plural(Number(job.data.result.observations ?? 0), 'suggestion')} to review below.
        </p>
      )}
      {current.length === 0 ? (
        <p className="muted">Nothing has been described yet.</p>
      ) : (
        <>
          {waiting > 0 && job.data?.status !== 'succeeded' && (
            <p className="small">{plural(waiting, 'suggestion')} waiting for you.</p>
          )}
          <ul className="picture-notes">
            {current.map((observation) => (
              <Observation
                key={observation.id}
                picture={picture}
                observation={observation}
                marked={marked?.id === observation.id}
                onMark={onMark}
              />
            ))}
          </ul>
        </>
      )}
      {earlier.length > 0 && (
        <details className="more">
          <summary>Rejected and earlier suggestions ({earlier.length})</summary>
          <ul className="picture-notes quiet">
            {earlier.map((observation) => (
              <li key={observation.id} className="picture-note">
                <p className="picture-note-words">{observation.words}</p>
                <p className="small muted">
                  {(STATE_WORDS[observation.state] ?? { label: observation.state }).label}
                  {observation.current ? '' : ' · about an earlier version of the picture'}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

/**
 * One picture (AST-007): the picture, its details, where it is shown and who may know
 * what it shows, what the assistant saw in it for the author to review, and how the
 * assistant is given it.
 */
export function PicturePage() {
  const { assetId = '' } = useParams()
  const { scope } = useProject()
  const [marked, setMarked] = useState<PictureObservation | null>(null)
  const picture = useQuery({
    queryKey: scopedKey(scope, 'picture-page', assetId),
    queryFn: () => pictureApi.picture(scope, assetId),
  })
  if (picture.isPending) return <Loading>Opening the picture…</Loading>
  const found = picture.data
  if (!found) {
    return (
      <div className="stack">
        <ErrorNotice error={picture.error} prefix="Could not open this picture" />
        <p>
          <Link to={`/projects/${scope.projectId}/media`}>Back to media</Link>
        </p>
      </div>
    )
  }
  const version = found.version
  const facts = [
    `Version ${version.version}`,
    TYPE_WORDS[version.content_type] ?? version.content_type,
    size(version.byte_length),
    found.width && found.height ? `${found.width} × ${found.height} pixels` : null,
  ].filter(Boolean)
  return (
    <div className="picture-page">
      <header className="stack tight picture-head">
        <p className="eyebrow">
          <Link to={`/projects/${scope.projectId}/media`}>Media</Link> · Picture
        </p>
        <h1>{found.name || 'A picture'}</h1>
        <p className="muted">{facts.join(' · ')}</p>
        {(version.source || version.licence || version.attribution) && (
          <p className="small muted">
            {[version.attribution && `By ${version.attribution}`, version.licence && `Licence: ${version.licence}`, version.source && `From ${version.source}`]
              .filter(Boolean)
              .join(' · ')}
          </p>
        )}
      </header>
      <div className="picture-layout">
        <PictureMat picture={found} marked={marked} />
        <div className="picture-column">
          <Describe picture={found} marked={marked} onMark={setMarked} />
          <ContextChoice picture={found} />
          <WhereShown picture={found} />
          <NewVersion picture={found} />
          {found.versions.length > 1 && (
            <p className="small muted">
              Older versions: {found.versions.slice(1).map((item) => `version ${item.version}`).join(', ')}. What
              the assistant saw in them stays with them.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
