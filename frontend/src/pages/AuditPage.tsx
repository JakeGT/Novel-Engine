import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type AuditScene, type CheckPass } from '../api/client'
import { JobProgress } from '../components/StudioWork'
import { Badge, ErrorNotice, Loading } from '../components/ui'
import { AUDIT_STATUS, NOTE_KINDS, PASS_NAMES, isFinished, scenePath } from '../lib/studio'
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

      <ol className="audit-scenes">
        {found.scenes.map((scene) => (
          <SceneResult key={`${scene.plan_id}:${scene.version_id}`} scene={scene} />
        ))}
      </ol>

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
