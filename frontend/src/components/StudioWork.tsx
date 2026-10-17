import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type SceneDraft, type StudioJob } from '../api/client'
import { LENGTHS, MAX_DIRECTION, MAX_DRAFTS, isDraftJob, type DraftChoices } from '../lib/studio'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { AllowanceNote } from './Allowance'
import { AssistantProblem } from './AssistFlow'
import { Button, ErrorNotice, Field } from './ui'

const number = (value: number) => value.toLocaleString('en-GB')

/**
 * What to ask for (how many drafts, how long, any direction) and, before anything is
 * sent, what it will read and roughly what it will cost. The choices live with the page,
 * so closing this keeps what was typed.
 */
export function DraftForm({
  planId,
  choices,
  onChange,
  onStart,
  onClose,
  onShowContext,
  starting,
  error,
  busy,
}: {
  planId: string
  choices: DraftChoices
  onChange: (next: DraftChoices) => void
  onStart: () => void
  onClose: () => void
  onShowContext?: () => void
  starting: boolean
  error: unknown
  /** Other work for this scene is still going; one thing at a time. */
  busy: boolean
}) {
  const { scope } = useProject()
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'draft-estimate', planId, choices.variants, choices.words),
    queryFn: () =>
      api.draftEstimate(scope, planId, { variants: choices.variants, words: choices.words, direction: '' }),
    staleTime: 60_000,
  })
  const found = estimate.data
  const set = (part: Partial<DraftChoices>) => onChange({ ...choices, ...part })
  const many = choices.variants > 1

  return (
    <section className="studio-ask stack" aria-labelledby="ask-heading">
      <div>
        <h2 id="ask-heading">Draft with the assistant</h2>
        <p className="muted small">
          Drafts wait beside your text. Nothing replaces your words unless you keep a draft.
        </p>
      </div>
      <fieldset>
        <legend>How many drafts</legend>
        <div className="segmented" role="group" aria-label="How many drafts">
          {Array.from({ length: MAX_DRAFTS }, (_, index) => index + 1).map((count) => (
            <button
              key={count}
              type="button"
              aria-pressed={choices.variants === count}
              onClick={() => set({ variants: count })}
            >
              {count}
            </button>
          ))}
        </div>
        <p className="hint">Several drafts give you different takes on the same scene to choose from.</p>
      </fieldset>
      <Field label="How long">
        {(props) => (
          <select
            {...props}
            className="studio-length"
            value={String(choices.words)}
            onChange={(event) => set({ words: Number(event.target.value) })}
          >
            {LENGTHS.map((length) => (
              <option key={length.value} value={String(length.value)}>
                {length.label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        label="Anything it should do (optional)"
        hint="What must happen, a mood, a line to include. Your plan for the scene and your notes on how you write are always given."
      >
        {(props) => (
          <textarea
            {...props}
            rows={3}
            maxLength={MAX_DIRECTION}
            value={choices.direction}
            placeholder="She doesn’t say why she came back. End before he answers."
            onChange={(event) => set({ direction: event.target.value })}
          />
        )}
      </Field>

      <div className="studio-cost" role="note" aria-live="polite">
        {estimate.isPending && <span className="muted">Working out what it will read…</span>}
        {found && (
          <p>
            It will read about <strong>{number(found.context_words)} words</strong> from your story
            bible, plan and writing, and write about{' '}
            <strong>{number(choices.words * choices.variants)} words</strong>
            {many ? ` across ${choices.variants} drafts` : ''}. About {number(found.total_tokens)} tokens in all (the measure AI
            providers charge by).
          </p>
        )}
        {estimate.error ? <span className="muted">It could not work out the cost just now.</span> : null}
        {onShowContext && (
          <button type="button" className="link-button small" onClick={onShowContext}>
            See exactly what it would read
          </button>
        )}
        <AllowanceNote />
      </div>

      <AssistantProblem error={error} />
      <div className="actions">
        <Button variant="primary" icon="spark" disabled={starting || busy} onClick={onStart}>
          {starting ? 'Starting…' : many ? `Write ${choices.variants} drafts` : 'Write a draft'}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Not now
        </Button>
        {busy && <span className="hint">Wait for the work in progress to finish, or stop it.</span>}
      </div>
    </section>
  )
}

const DOING: Record<string, string> = {
  scene_draft: 'The assistant is drafting this scene',
  scene_review: 'The assistant is reading your text',
}

/**
 * Work in progress: what stage it is at, how far along, and a way to stop it. It keeps
 * going if the author leaves the page, so it says so.
 */
export function JobProgress({
  job,
  onStop,
  stopping,
  error,
}: {
  job: StudioJob
  onStop: () => void
  stopping: boolean
  error: unknown
}) {
  const { canWrite } = useProject()
  const asked = job.cancel_requested || stopping
  return (
    <section className="studio-job" aria-label="Work in progress">
      <div className="studio-job-head">
        <span className="spinner" aria-hidden="true" />
        <p aria-live="polite">
          <strong>{DOING[job.kind] ?? 'The assistant is working'}.</strong>{' '}
          {asked ? 'Stopping after this step…' : job.stage || 'Waiting to start…'}
        </p>
        {canWrite && (
          <Button size="sm" onClick={onStop} disabled={asked}>
            {asked ? 'Stopping…' : 'Stop'}
          </Button>
        )}
      </div>
      <progress className="meter" max={100} value={job.progress} aria-label="How far along" />
      <p className="hint">
        You can keep writing while it works. Leaving this page does not stop it; come back to find
        the result here.
      </p>
      <ErrorNotice error={error} prefix="Could not stop it" />
    </section>
  )
}

/** What finished work came to, with the one next step it suggests. */
export function JobOutcome({
  job,
  drafts,
  onRead,
  onRetry,
  onDismiss,
}: {
  job: StudioJob
  /** The drafts this work wrote that are still waiting. */
  drafts: SceneDraft[]
  onRead: (draftId: string) => void
  onRetry: () => void
  onDismiss: () => void
}) {
  const { scope, access } = useProject()
  const drafting = isDraftJob(job)
  const close = (
    <Button size="sm" variant="ghost" onClick={onDismiss}>
      Dismiss
    </Button>
  )
  if (job.status === 'failed') {
    return (
      <div className="notice danger" role="alert">
        <p>
          <strong>{drafting ? 'The draft was not written.' : 'The check did not finish.'}</strong>{' '}
          {job.error_message || 'Something went wrong on our side.'} Your text is unchanged.
        </p>
        <div className="actions">
          {job.error_code === 'no_provider' && access?.hosted && (
            <Link className="btn sm" to={`/projects/${scope.projectId}/assistant`}>
              Connect an AI key
            </Link>
          )}
          <Button size="sm" onClick={onRetry}>
            Try again
          </Button>
          {close}
        </div>
      </div>
    )
  }
  if (job.status === 'cancelled') {
    return (
      <div className="notice plain" role="status">
        <p>
          <strong>Stopped.</strong>{' '}
          {drafts.length > 0
            ? `${plural(drafts.length, 'draft')} finished before it stopped and ${drafts.length === 1 ? 'is' : 'are'} waiting for you.`
            : 'Nothing was kept from it. Your text is unchanged.'}
        </p>
        <div className="actions">{close}</div>
      </div>
    )
  }
  if (drafting) {
    // Every draft it wrote has been kept or discarded: nothing is waiting.
    if (drafts.length === 0) return null
    return (
      <div className="notice ok" role="status">
        <p>
          <strong>{drafts.length === 1 ? 'A draft is ready.' : `${plural(drafts.length, 'draft')} are ready.`}</strong>{' '}
          Read {drafts.length === 1 ? 'it' : 'them'}, then keep one or discard what you do not want.
        </p>
        <div className="actions">
          {drafts[0] && (
            <Button size="sm" variant="primary" onClick={() => onRead(drafts[0].id)}>
              Read the {drafts.length === 1 ? 'draft' : 'first one'}
            </Button>
          )}
          {close}
        </div>
      </div>
    )
  }
  const found = Number(job.result.findings ?? 0)
  const dropped = Number(job.result.dropped ?? 0)
  return (
    <div className="notice ok" role="status">
      <p>
        <strong>
          {found === 0 ? 'The assistant found nothing to point out.' : `The assistant made ${plural(found, 'note')}.`}
        </strong>{' '}
        {found > 0 ? 'They are beside the text, each pointing at its passage. ' : ''}
        {dropped > 0
          ? `${plural(dropped, 'note')} did not quote your words exactly, so ${dropped === 1 ? 'it was' : 'they were'} left out.`
          : ''}
      </p>
      <div className="actions">{close}</div>
    </div>
  )
}
