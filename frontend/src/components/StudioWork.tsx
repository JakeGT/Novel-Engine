import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type SceneDraft, type StudioJob } from '../api/client'
import {
  JOB_DOING,
  LENGTHS,
  MAX_DIRECTION,
  MAX_DRAFTS,
  isDraftJob,
  isPlanJob,
  isReadJob,
  planCardProposal,
  readingProposals,
  reviewPath,
  useSuggestedChanges,
  type DraftChoices,
} from '../lib/studio'
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
          <strong>{JOB_DOING[job.kind] ?? 'The assistant is working'}.</strong>{' '}
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
  const reading = isReadJob(job)
  const planning = isPlanJob(job)
  const close = (
    <Button size="sm" variant="ghost" onClick={onDismiss}>
      Dismiss
    </Button>
  )
  if (job.status === 'failed') {
    return (
      <div className="notice danger" role="alert">
        <p>
          <strong>
            {drafting
              ? 'The draft was not written.'
              : reading
                ? 'The scene was not read into your story.'
                : planning
                  ? 'The plan card was not filled in.'
                  : 'The check did not finish.'}
          </strong>{' '}
          {job.error_message || 'Something went wrong on our side.'}{' '}
          {reading ? 'Your story is unchanged.' : planning ? 'Your plan is unchanged.' : 'Your text is unchanged.'}
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
          {reading
            ? 'Anything it suggested before it stopped is waiting in the review queue. Your story is unchanged.'
            : planning
              ? 'Nothing was suggested for the plan card. Your plan is unchanged.'
              : drafts.length > 0
              ? `${plural(drafts.length, 'draft')} finished before it stopped and ${drafts.length === 1 ? 'is' : 'are'} waiting for you.`
              : 'Nothing was kept from it. Your text is unchanged.'}
        </p>
        <div className="actions">
          {reading && (
            <Link className="btn sm" to={reviewPath(scope.projectId, [])}>
              Open the review queue
            </Link>
          )}
          {close}
        </div>
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
          <Used job={job} />
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
  if (reading) return <ReadingOutcome job={job} close={close} />
  if (planning) return <PlanCardOutcome job={job} close={close} />
  const found = Number(job.result.findings ?? 0)
  const dropped = Number(job.result.dropped ?? 0)
  return (
    <div className="notice ok" role="status">
      <p>
        {job.automatic && 'Checked on its own, as this story is set to. '}
        <strong>
          {found === 0 ? 'The assistant found nothing to point out.' : `The assistant made ${plural(found, 'note')}.`}
        </strong>{' '}
        {found > 0 ? 'They are beside the text, each pointing at its passage. ' : ''}
        {dropped > 0
          ? `${plural(dropped, 'note')} did not quote your words exactly, so ${dropped === 1 ? 'it was' : 'they were'} left out.`
          : ''}
        <Used job={job} />
      </p>
      <div className="actions">{close}</div>
    </div>
  )
}

/**
 * What finished work used, as the provider reported it (OPS-002): tokens always, and the
 * cost only when the provider said what it was. No price is ever worked out here.
 */
function Used({ job }: { job: StudioJob }) {
  const tokens = job.prompt_tokens + job.completion_tokens
  if (tokens <= 0) return null
  const cost = job.provider_cost_usd
  return (
    <span className="muted">
      {' '}
      It used {number(tokens)} tokens
      {cost !== null && cost !== undefined
        ? ` (${cost < 0.01 ? 'under 1 cent' : `$${cost.toFixed(2)}`}, as your provider reported)`
        : ''}
      .
    </span>
  )
}

/** What reading the scene into the story came to: suggestions to review, or nothing new. */
function ReadingOutcome({ job, close }: { job: StudioJob; close: ReactNode }) {
  const { scope } = useProject()
  const proposals = readingProposals(job)
  const count = useSuggestedChanges(proposals)
  const left = Number(job.result.discarded ?? 0)
  const leftOut =
    left > 0
      ? ` ${plural(left, 'thing')} it found could not be tied to the scene’s words, so ${left === 1 ? 'it was' : 'they were'} left out.`
      : ''
  if (proposals.length === 0) {
    return (
      <div className="notice plain" role="status">
        <p>
          <strong>Nothing new to add from this scene.</strong> Everything it states is already in
          your story bible.{leftOut}
        </p>
        <div className="actions">{close}</div>
      </div>
    )
  }
  return (
    <div className="notice ok" role="status">
      <p>
        <strong>
          {count === null ? 'Changes suggested from this scene.' : `${plural(count, 'change')} suggested from this scene.`}
        </strong>{' '}
        Nothing changes in your story until you review {count === 1 ? 'it' : 'them'}.{leftOut}
      </p>
      <div className="actions">
        <Link className="btn primary sm" to={reviewPath(scope.projectId, proposals)}>
          {count === 1 ? 'Review it' : 'Review them'}
        </Link>
        {close}
      </div>
    </div>
  )
}

/** What filling in the plan card came to: one plan change to review, or nothing to add. */
function PlanCardOutcome({ job, close }: { job: StudioJob; close: ReactNode }) {
  const { scope } = useProject()
  const proposal = planCardProposal(job)
  const summary = job.result.summary_filled === true
  const refs = Number(job.result.refs_added ?? 0)
  const left = Number(job.result.dropped ?? 0)
  const leftOut =
    left > 0
      ? ` ${plural(left, 'suggestion')} could not be tied to the scene’s words, so ${left === 1 ? 'it was' : 'they were'} left out.`
      : ''
  if (!proposal) {
    return (
      <div className="notice plain" role="status">
        <p>
          <strong>Nothing to add to the plan card.</strong> The assistant suggests only what the
          scene’s words show, and never replaces what you planned. Your plan is unchanged.{leftOut}
        </p>
        <div className="actions">{close}</div>
      </div>
    )
  }
  const parts = [
    summary ? 'a summary' : null,
    refs > 0 ? plural(refs, 'person or place', 'people or places') : null,
  ].filter((part): part is string => part !== null)
  return (
    <div className="notice ok" role="status">
      <p>
        <strong>
          {parts.length > 0
            ? `The assistant suggests ${parts.join(' and ')} for this scene’s plan card.`
            : 'The assistant suggests changes to this scene’s plan card.'}
        </strong>{' '}
        Nothing changes in your plan until you review it.{leftOut}
      </p>
      <div className="actions">
        <Link className="btn primary sm" to={reviewPath(scope.projectId, [proposal])}>
          Review it
        </Link>
        {close}
      </div>
    </div>
  )
}
