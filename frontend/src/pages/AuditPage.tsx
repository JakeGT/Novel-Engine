import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, isApiError, type AuditScene, type CheckPass, type CorrectionPlan } from '../api/client'
import { AuditRecord } from '../components/CheckHistory'
import { JobProgress } from '../components/StudioWork'
import { Badge, Button, ErrorNotice, Loading } from '../components/ui'
import { AUDIT_STATUS, NOTE_KINDS, PASS_NAMES, isFinished, scenePath, tokenCount } from '../lib/studio'
import { fullDate } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'

/**
 * A deep audit's results (CON-011), scene by scene: what the assistant noted in the
 * version it read, the checks without the assistant, and the plan's own checks. Each
 * passage links to the studio, which selects those words in the scene as it is now.
 */
export function AuditPage() {
  const { jobId = '' } = useParams()
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'audit', jobId)
  // The full report reads every scene again, so while the audit runs only the job is
  // followed closely; the report is read again now and then, and once the job ends.
  const audit = useQuery({
    queryKey: key,
    queryFn: () => api.audit(scope, jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data.job) ? false : REPORT_EVERY),
  })
  const running = Boolean(audit.data && !isFinished(audit.data.job))
  const jobKey = scopedKey(scope, 'audit-job', jobId)
  const live = useQuery({
    queryKey: jobKey,
    queryFn: () => api.job(scope, jobId),
    enabled: running,
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const ended = Boolean(running && live.data && isFinished(live.data))
  useEffect(() => {
    if (ended) void client.invalidateQueries({ queryKey: scopedKey(scope, 'audit', jobId) })
  }, [ended, client, scope, jobId])
  const stop = useMutation({
    mutationFn: () => api.cancelJob(scope, jobId),
    onSuccess: () => void client.invalidateQueries({ queryKey: jobKey }),
  })

  if (audit.isPending) return <Loading>Opening the audit…</Loading>
  const found = audit.data
  if (!found) {
    return (
      <div className="stack">
        <ErrorNotice error={audit.error} prefix="Could not open this audit" />
        <p>
          <Link to={`/projects/${scope.projectId}/plan`}>Back to the story plan</Link>
        </p>
      </div>
    )
  }

  // The job as last followed while it runs; the report's own once it has ended.
  const job = running && live.data ? live.data : found.job
  const finished = isFinished(found.job)
  const notes = found.scenes.reduce((sum, scene) => sum + scene.notes.length, 0)
  const checks = found.scenes.reduce((sum, scene) => sum + scene.checks.length + scene.story_checks.length, 0)
  const checked = found.scenes.filter((scene) => scene.status === 'checked').length
  const passNames = found.passes.map((item) => PASS_NAMES[item])

  return (
    <div className="stack audit-page">
      <header className="stack tight">
        <p className="eyebrow">
          <Link to={`/projects/${scope.projectId}/plan`}>Story plan</Link> · Audit
        </p>
        <h1>Audit of “{found.title}”</h1>
        <p className="audit-summary">
          <span>Started {fullDate(job.created_at)}</span>
          <span>{AUDIT_STATUS[job.status] ?? job.status}</span>
          <span>
            {found.passes.length === 0
              ? 'Checks without the assistant only'
              : `Assistant checks: ${passNames.join(', ')}`}
          </span>
          {job.prompt_tokens + job.completion_tokens > 0 && (
            <span>{(job.prompt_tokens + job.completion_tokens).toLocaleString('en-GB')} tokens used</span>
          )}
        </p>
      </header>

      {!finished && !isFinished(job) && (
        <JobProgress job={job} stopping={stop.isPending} error={stop.error} onStop={() => stop.mutate()} />
      )}
      {found.job.status === 'cancelled' && (
        <p className="notice plain" role="status">
          The audit was stopped after {checked} of {plural(found.scenes.length, 'scene')}. What it
          finished is below.
        </p>
      )}
      {found.job.status === 'failed' && (
        <p className="notice warn" role="alert">
          The audit did not finish: {found.job.error_message ?? 'something went wrong.'} What it
          finished is below.
        </p>
      )}
      {finished && (
        <p role="status">
          {notes === 0 && checks === 0
            ? `Nothing to point out in ${plural(checked, 'scene')}.`
            : `${plural(notes, 'note')} from the assistant and ${plural(checks, 'check')} without it, across ${plural(checked, 'scene')}.`}
        </p>
      )}

      {finished && <AuditRecord jobId={jobId} refresh={found.job.status} />}

      {finished && <Corrections jobId={jobId} />}

      <ol className="audit-scenes">
        {found.scenes.map((scene) => (
          <SceneResult key={`${scene.plan_id}:${scene.version_id}`} scene={scene} />
        ))}
      </ol>

      {finished && found.scenes.length > 0 && <Drift jobId={jobId} />}

      {found.unwritten.length > 0 && (
        <section className="stack tight" aria-labelledby="unwritten-heading">
          <h2 id="unwritten-heading" className="audit-group-title">
            Not written yet, so not audited
          </h2>
          <ul className="rows">
            {found.unwritten.map((item) => (
              <li className="row" key={item.plan_id}>
                <Link to={scenePath(scope.projectId, item.plan_id)}>{item.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

const REPORT_EVERY = 15_000
const JOB_EVERY = 2_000

const STATUS: Record<string, { label: string; tone: 'ok' | 'neutral' | 'info' }> = {
  checked: { label: 'Checked', tone: 'ok' },
  partial: { label: 'Partly checked: the audit stopped first', tone: 'neutral' },
  not_checked: { label: 'Not checked: the audit stopped first', tone: 'neutral' },
  gone: { label: 'Not checked: removed from the plan', tone: 'neutral' },
  waiting: { label: 'Waiting or being checked', tone: 'info' },
}

function SceneResult({ scene }: { scene: AuditScene }) {
  const { scope } = useProject()
  const status = STATUS[scene.status] ?? STATUS.waiting
  const done = scene.passes_done ?? []
  const label =
    scene.status === 'partial' && done.length > 0
      ? `Partly checked: ${done.map((item) => PASS_NAMES[item]).join(', ')}`
      : status.label
  const headingId = `audit-scene-${scene.plan_id}`
  const link = (start?: number | null, end?: number | null) =>
    scene.in_plan && start !== null && start !== undefined && end !== null && end !== undefined
      ? scenePath(scope.projectId, scene.plan_id, { versionId: scene.version_id, start, end })
      : null
  const byPass = new Map<CheckPass | 'other', typeof scene.notes>()
  for (const note of scene.notes) {
    const group = note.check_pass ?? 'other'
    byPass.set(group, [...(byPass.get(group) ?? []), note])
  }
  const empty = scene.notes.length === 0 && scene.checks.length === 0 && scene.story_checks.length === 0
  return (
    <li className="audit-scene" aria-labelledby={headingId}>
      <div className="audit-scene-head">
        <h2 id={headingId}>{scene.title}</h2>
        {scene.in_plan && (
          <Link className="btn sm" to={scenePath(scope.projectId, scene.plan_id)}>
            Open in the studio
          </Link>
        )}
      </div>
      <p className="audit-summary">
        <Badge tone={status.tone}>{label}</Badge>
        {scene.version_number !== null && scene.version_number !== undefined && (
          <span>Version {scene.version_number}</span>
        )}
        {!scene.current && scene.in_plan && <span>The scene has changed since; passages are found in it as it is now.</span>}
      </p>
      {scene.status === 'checked' && empty && <p className="muted small">Nothing to point out.</p>}
      {[...byPass.entries()].map(([group, items]) => (
        <section className="stack tight" key={group} aria-label={group === 'other' ? 'Notes' : PASS_NAMES[group]}>
          <h3 className="audit-group-title">{group === 'other' ? 'From the assistant' : PASS_NAMES[group]}</h3>
          <ul className="findings">
            {items.map((note) => (
              <li className="finding assistant" key={note.id}>
                {/* Under a check's own heading its name would only repeat; older notes say their kind. */}
                {group === 'other' && <Badge tone="info">{NOTE_KINDS[note.category] ?? 'Note'}</Badge>}
                <p>{note.message}</p>
                {note.suggestion && <p className="small finding-suggestion">Suggestion: {note.suggestion}</p>}
                <Quote text={note.quoted} to={link(note.start, note.end)} />
              </li>
            ))}
          </ul>
        </section>
      ))}
      {scene.checks.length > 0 && (
        <section className="stack tight" aria-label="Checks without the assistant">
          <h3 className="audit-group-title">Checks without the assistant</h3>
          <ul className="findings">
            {scene.checks.map((check, index) => (
              <li className={`finding ${check.severity}`} key={`${check.code}-${index}`}>
                <Badge tone={check.severity === 'warning' ? 'warn' : 'neutral'}>
                  {check.severity === 'warning' ? 'Worth checking' : 'Note'}
                </Badge>
                <p>{check.message}</p>
                <Quote text={check.quoted ?? ''} to={link(check.start, check.end)} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {scene.story_checks.length > 0 && (
        <section className="stack tight" aria-label="In the plan">
          <h3 className="audit-group-title">In the plan</h3>
          <ul className="findings">
            {scene.story_checks.map((message, index) => (
              <li className="finding warning" key={index}>
                <Badge tone="warn">Worth checking</Badge>
                <p>{message}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </li>
  )
}

function Quote({ text, to }: { text: string; to: string | null }) {
  if (!text) return null
  return (
    <p className="finding-passage">
      <q>{text}</q> {to && <Link to={to}>Show in the studio</Link>}
    </p>
  )
}

// --- across the scenes: voice and relationships (CON-018, CON-011) -------------------

function percent(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

/** How voice and relationships move across the scenes the audit read. Free, no model. */
function Drift({ jobId }: { jobId: string }) {
  const { scope } = useProject()
  const drift = useQuery({
    queryKey: scopedKey(scope, 'audit-drift', jobId),
    queryFn: () => api.auditDrift(scope, jobId),
    retry: false,
  })
  const found = drift.data
  return (
    <section className="audit-extra" aria-labelledby="drift-heading">
      <h2 id="drift-heading">Voice and relationships across these scenes</h2>
      <p className="hint">
        Worked out from the words and the story bible, with no assistant: how each scene sounds
        against your usual, and how each pair of characters moves through these scenes.
      </p>
      {drift.isPending && <Loading>Measuring the scenes…</Loading>}
      <ErrorNotice error={drift.error} prefix="Could not measure these scenes" />
      {found && (
        <>
          {!found.usual.enough && (
            <p className="muted small">
              Not enough of your writing yet to know your usual: it needs {found.usual.needs_scenes}{' '}
              scenes and {(found.usual.needs_words ?? 0).toLocaleString('en-GB')} words of your own.
            </p>
          )}
          {found.trends.length > 0 && (
            <ul className="findings">
              {found.trends.map((trend) => (
                <li className="finding note" key={trend}>
                  <Badge>Across the scenes</Badge>
                  <p>{trend}</p>
                </li>
              ))}
            </ul>
          )}
          <div className="table-scroll">
            <table className="drift-table">
              <caption className="sr-only">Each scene’s voice, in reading order</caption>
              <thead>
                <tr>
                  <th scope="col">Scene</th>
                  <th scope="col">Sentence length</th>
                  <th scope="col">Dialogue</th>
                  <th scope="col">How it departs from your usual</th>
                </tr>
              </thead>
              <tbody>
                {found.voice.map((row) => (
                  <tr key={row.plan_id}>
                    <th scope="row">
                      <Link to={scenePath(scope.projectId, row.plan_id)}>{row.title}</Link>
                    </th>
                    <td>about {Math.round(row.stats.sentence_mean)} words</td>
                    <td>{percent(row.stats.dialogue_share)}</td>
                    <td>
                      {row.notes.length === 0 ? (
                        <span className="muted">As usual</span>
                      ) : (
                        row.notes.map((note, index) => <p key={`${note.code}-${index}`}>{note.message}</p>)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {found.usual.enough && (
            <p className="hint">
              Your usual across the book: sentences of about {Math.round(found.usual.sentence_mean ?? 0)} words and about{' '}
              {percent(found.usual.dialogue_share)} dialogue, from {plural(found.usual.scenes, 'scene')}
              {found.usual.own_only ? ' you wrote yourself' : ''}.
            </p>
          )}
          <h3 className="audit-group-title">Relationships</h3>
          {found.relationships.length === 0 ? (
            <p className="muted small">No two characters share these scenes or a fact between them.</p>
          ) : (
            <ul className="rows">
              {found.relationships.map((pair) => (
                <li className="row stack tight" key={pair.names.join('|')}>
                  <strong>
                    {pair.names[0]} and {pair.names[1]}
                  </strong>
                  <span className="small muted">
                    Together in {plural(pair.together.length, 'scene')}
                    {pair.recorded.length > 0 ? ` · the story bible says: ${pair.recorded.join('; ')}` : ''}
                  </span>
                  {pair.notes.map((note) => (
                    <span className="small" key={note}>
                      <Badge tone="warn">Worth checking</Badge> {note}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

// --- a correction plan from the audit's findings (CON-011) ---------------------------

const DECISIONS: { value: 'kept' | 'skipped'; label: string }[] = [
  { value: 'kept', label: 'Do this' },
  { value: 'skipped', label: 'Not now' },
]

/**
 * "Plan the corrections": the assistant suggests which scenes to change and why, each
 * step citing the findings it answers. Cost first; nothing in the story changes, and the
 * author keeps or skips each step and makes the change in the studio themselves.
 */
function Corrections({ jobId }: { jobId: string }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'corrections', jobId)
  const plans = useQuery({
    queryKey: key,
    queryFn: () => api.corrections(scope, jobId),
    refetchInterval: (query) => (query.state.data?.jobs.some((job) => !isFinished(job)) ? 2_000 : false),
  })
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'corrections-estimate', jobId),
    queryFn: () => api.correctionsEstimate(scope, jobId),
    enabled: canWrite,
    retry: false,
  })
  const start = useMutation({
    mutationFn: () => api.planCorrections(scope, jobId),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })
  const stop = useMutation({
    mutationFn: (id: string) => api.cancelJob(scope, id),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })
  const running = plans.data?.jobs.find((job) => !isFinished(job))
  const failed = plans.data?.jobs.find((job) => job.status === 'failed')
  const latest = plans.data?.plans[0]
  const nothing = Boolean(estimate.error && isApiError(estimate.error, 'validation_error'))
  return (
    <section className="audit-extra" aria-labelledby="corrections-heading">
      <h2 id="corrections-heading">Correction plan</h2>
      <p className="hint">
        The assistant suggests which scenes to change and why, from this audit’s findings. It writes
        no words and changes nothing; you keep or skip each step and make the change yourself.
      </p>
      {plans.isPending && <Loading>Looking for a correction plan…</Loading>}
      <ErrorNotice error={plans.error} prefix="Could not open the correction plan" />
      {running && (
        <JobProgress job={running} stopping={stop.isPending} error={stop.error} onStop={() => stop.mutate(running.id)} />
      )}
      {!running && failed && !latest && (
        <p className="notice warn" role="alert">
          The plan was not made: {failed.error_message ?? 'something went wrong.'}
        </p>
      )}
      {latest && <PlanSteps plan={latest} canWrite={canWrite} />}
      {canWrite && !running && (
        <div className="stack tight">
          {nothing ? (
            <p className="muted small">The audit found nothing to correct.</p>
          ) : (
            <>
              <div className="actions">
                <Button
                  icon="spark"
                  variant={latest ? 'default' : 'primary'}
                  disabled={start.isPending || !estimate.data}
                  aria-describedby="corrections-cost"
                  onClick={() => start.mutate()}
                >
                  {start.isPending ? 'Starting…' : latest ? 'Plan the corrections again' : 'Plan the corrections'}
                </Button>
              </div>
              <p className="hint" id="corrections-cost">
                {estimate.data
                  ? `Reads ${plural(estimate.data.findings, 'finding')} across ${plural(estimate.data.scenes, 'scene')}: about ${tokenCount(estimate.data.total_tokens)} tokens.`
                  : estimate.isPending
                    ? 'Working out what it would use…'
                    : ''}
              </p>
              <ErrorNotice error={nothing ? null : estimate.error} prefix="Could not work out what it would use" />
              {start.error && isApiError(start.error, 'not_confirmed') ? (
                <p className="hint" role="status">
                  Not started, as you chose. Nothing was spent.
                </p>
              ) : (
                <ErrorNotice error={start.error} prefix="Could not start planning" />
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}

function PlanSteps({ plan, canWrite }: { plan: CorrectionPlan; canWrite: boolean }) {
  const { scope } = useProject()
  const client = useQueryClient()
  const decide = useMutation({
    mutationFn: ({ index, decision }: { index: number; decision: 'waiting' | 'kept' | 'skipped' }) =>
      api.decideCorrection(scope, plan.id, index, decision),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'corrections') }),
  })
  const kept = plan.items.filter((item) => item.decision === 'kept').length
  return (
    <div className="stack">
      {plan.summary && <p>{plan.summary}</p>}
      <p className="small muted" role="status">
        {plural(plan.items.length, 'step')}
        {kept > 0 ? `, ${kept} to do` : ''}
        {plan.dropped > 0 ? ` · ${plural(plan.dropped, 'suggestion')} left out for citing no finding of this audit` : ''}
      </p>
      {plan.items.length === 0 ? (
        <p className="muted small">The assistant suggested nothing this audit’s findings support.</p>
      ) : (
        <ol className="correction-steps">
          {plan.items.map((item, index) => (
            <li className={`correction-step ${item.decision}`} key={`${item.plan_id}-${index}`}>
              <div className="audit-scene-head">
                <strong>{item.change}</strong>
                <Link className="small" to={scenePath(scope.projectId, item.plan_id)}>
                  Open “{item.title}” in the studio
                </Link>
              </div>
              {item.why && <p className="small">{item.why}</p>}
              <ul className="cites small" aria-label="The findings it answers">
                {item.cited.map((cite) => (
                  <li key={cite}>{cite}</li>
                ))}
              </ul>
              {canWrite && (
                <div className="segmented" role="group" aria-label={`Step ${index + 1}: ${item.change}`}>
                  {DECISIONS.map((choice) => (
                    <button
                      key={choice.value}
                      type="button"
                      aria-pressed={item.decision === choice.value}
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({ index, decision: item.decision === choice.value ? 'waiting' : choice.value })
                      }
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
      <ErrorNotice error={decide.error} prefix="Not changed" />
    </div>
  )
}
