import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type HelpEstimate, type PlanItem, type StudioJob } from '../api/client'
import { isFinished, tokenCount } from '../lib/studio'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import { JobProgress } from './StudioWork'
import { Button, ErrorNotice, Loading } from './ui'

const JOB_EVERY = 1500

/**
 * One kind of help from the assistant, offered where it is needed. Opening it works out
 * what it would use and says so before anything is sent; starting it queues the work,
 * and the result is a suggestion waiting in Review. Nothing in the plan changes here.
 */
function HelpOffer({
  offer,
  what,
  estimateKey,
  estimate,
  start,
  outcome,
}: {
  /** The link that opens the offer: says what the help does. */
  offer: string
  /** What is sent, in plain words, shown with the cost. */
  what: (cost: HelpEstimate) => string
  estimateKey: unknown[]
  estimate: () => Promise<HelpEstimate>
  start: () => Promise<StudioJob>
  /** What a finished job came to, in words, when it suggested nothing. */
  outcome: (job: StudioJob) => string
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const invalidate = useInvalidateProject(scope)
  const [open, setOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const cost = useQuery({ queryKey: scopedKey(scope, ...estimateKey), queryFn: estimate, enabled: open && !jobId })
  const job = useQuery({
    queryKey: scopedKey(scope, 'plan-help-job', jobId),
    queryFn: () => api.job(scope, jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const done = isFinished(job.data)
  // The plan and Review are read again once, when the work finishes.
  const refreshed = useRef<string | null>(null)
  useEffect(() => {
    if (done && jobId && refreshed.current !== jobId) {
      refreshed.current = jobId
      void invalidate()
    }
  }, [done, jobId, invalidate])
  const begin = useMutation({ mutationFn: start, onSuccess: (started) => setJobId(started.id) })
  const stop = useMutation({
    mutationFn: () => api.cancelJob(scope, jobId as string),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'plan-help-job', jobId) }),
  })

  if (!open) {
    return (
      <button type="button" className="link-button small" onClick={() => setOpen(true)}>
        {offer}
      </button>
    )
  }
  const running = job.data && !isFinished(job.data)
  const suggested = job.data?.status === 'succeeded' ? job.data.result.mutation_id : null
  return (
    <div className="plan-help stack tight" role="group" aria-label={offer}>
      {!jobId && (
        <>
          {cost.isPending && <Loading>Working out what it would use…</Loading>}
          <ErrorNotice error={cost.error ?? begin.error} prefix="Could not ask the assistant" />
          {cost.data && (
            <p className="small">
              {what(cost.data)} About {tokenCount(cost.data.total_tokens)} tokens. What it suggests waits in Review for
              you to keep, change or turn down.
            </p>
          )}
          <div className="actions">
            <Button size="sm" variant="primary" icon="spark" disabled={!cost.data || begin.isPending} onClick={() => begin.mutate()}>
              {begin.isPending ? 'Starting…' : 'Ask the assistant'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Not now
            </Button>
          </div>
        </>
      )}
      {job.data && running && (
        <JobProgress job={job.data} stopping={stop.isPending} error={stop.error} onStop={() => stop.mutate()} />
      )}
      {job.data?.status === 'failed' && (
        <p className="notice warn small" role="alert">
          It did not finish: {job.data.error_message ?? 'something went wrong.'}
        </p>
      )}
      {job.data?.status === 'cancelled' && <p className="small" role="status">Stopped. Nothing was suggested.</p>}
      {job.data?.status === 'succeeded' &&
        (typeof suggested === 'string' && suggested ? (
          <p className="small" role="status">
            Suggested.{' '}
            <Link to={`/projects/${scope.projectId}/review/${suggested}`}>See the suggestion in Review</Link>
          </p>
        ) : (
          <p className="small" role="status">
            {outcome(job.data)}
          </p>
        ))}
    </div>
  )
}

/** The assistant fills in what the viewpoint wants, what is in the way, and how it ends (STY-003). */
export function SuggestIntent({ scene }: { scene: PlanItem }) {
  const { scope } = useProject()
  const intent = scene.intent
  if (intent?.goal && intent.obstacle && intent.outcome) return null
  const offer = intent ? 'Suggest the rest of what this scene is for' : 'Suggest what this scene is for'
  return (
    <HelpOffer
      offer={offer}
      what={(cost) =>
        cost.reads_prose
          ? 'The assistant reads this scene’s plan card and what you have written of it, and fills in only what you have left empty.'
          : 'The assistant reads this scene’s plan card and fills in only what you have left empty.'
      }
      estimateKey={['plan-help', 'intent', scene.id, scene.created_revision, scene.summary, JSON.stringify(scene.intent ?? null)]}
      estimate={() => api.intentEstimate(scope, scene.id)}
      start={() => api.suggestIntent(scope, scene.id)}
      outcome={() => 'Nothing to suggest: the assistant could not show any of it from the plan card or the scene.'}
    />
  )
}

/** One or two scenes to come before this one and bring about what it needs (STY-004). */
export function SuggestBridges({ scene, index }: { scene: PlanItem; index: number }) {
  const { scope } = useProject()
  return (
    <HelpOffer
      offer="Suggest a scene to bridge this"
      what={() =>
        `The assistant reads what this needs, the scenes around “${scene.title}” and the entries involved, and suggests one or two scenes to come just before it.`
      }
      estimateKey={['plan-help', 'bridges', scene.id, index, JSON.stringify(scene.conditions)]}
      estimate={() => api.bridgeEstimate(scope, scene.id, index)}
      start={() => api.suggestBridges(scope, scene.id, index)}
      outcome={() => 'Nothing to suggest: none of the scenes the assistant offered fitted your story.'}
    />
  )
}
