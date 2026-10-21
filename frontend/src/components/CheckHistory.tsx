import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CheckRun, type CheckRunFinding } from '../api/client'
import { findingStatus, profileText, runLabel, tallyText, workText } from '../lib/checkRuns'
import { AUDIT_STATUS } from '../lib/studio'
import { fullDate, timeAgo } from '../lib/time'
import { scopedKey, useProject } from '../project'
import { Badge, Button, ErrorNotice, Loading } from './ui'

const TONE: Record<string, 'ok' | 'neutral' | 'warn'> = {
  open: 'warn',
  accepted: 'neutral',
  dismissed: 'neutral',
  superseded: 'neutral',
  fixed: 'ok',
}

function Tally(part: { open: number; accepted?: number; dismissed: number; superseded?: number; fixed: number }) {
  return <span>{tallyText(part)}</span>
}

/** How one pass was sent, when it could be sent in groups (CON-013). */
interface Sent {
  sent: 'groups' | 'whole'
  why?: string
  groups?: number
  complete?: boolean
  lines?: string[]
  passages?: { start: number; end: number; checks: string[] }[]
}

const PASS_WORDS: Record<string, string> = { continuity: 'Continuity with the story bible' }

/** For a check with the assistant: how a long scene was sent, and what each passage got. */
function Coverage({ run }: { run: CheckRun }) {
  const entries = Object.entries((run.coverage ?? {}) as Record<string, Sent>)
  if (entries.length === 0) return null
  return (
    <div className="stack tight run-coverage">
      {entries.map(([name, sent]) => {
        const label = PASS_WORDS[name.split(':').pop() ?? name] ?? 'A pass'
        if (sent.sent !== 'groups') {
          return (
            <p className="small" key={name}>
              {label}: sent as one request. {sent.why}
            </p>
          )
        }
        const kinds = new Map<string, number>()
        for (const passage of sent.passages ?? []) {
          for (const kind of passage.checks) kinds.set(kind, (kinds.get(kind) ?? 0) + 1)
        }
        return (
          <details className="run-findings" key={name}>
            <summary>
              {label}: sent in {sent.groups} groups.{' '}
              {sent.complete ? 'Every passage got every check it needs.' : 'Not every passage got every check it needs.'}
            </summary>
            <ul className="small plain-list">
              {(sent.lines ?? []).map((line) => (
                <li key={line}>{line}</li>
              ))}
              {[...kinds.entries()].map(([kind, count]) => (
                <li key={kind}>
                  {kind}: {count === 1 ? '1 passage' : `${count} passages`}
                </li>
              ))}
            </ul>
          </details>
        )
      })}
    </div>
  )
}

/** How often the author dismissed each check's findings in this story (CON-016). */
function Calibration() {
  const { scope } = useProject()
  const found = useQuery({ queryKey: scopedKey(scope, 'check-calibration'), queryFn: () => api.checkCalibration(scope) })
  const items = (found.data?.items ?? []).filter((item) => item.found > 0)
  return (
    <section className="stack tight" aria-labelledby="check-calibration-heading">
      <div>
        <h3 id="check-calibration-heading" className="check-calibration-title">
          How often you dismiss each check
        </h3>
        <p className="muted small">
          Across this story, so you can tell which checks are often wrong for it. A note for you: nothing about the
          checks changes on its own.
        </p>
      </div>
      {/* A note beside the history, not something to act on: said quietly when it cannot be read. */}
      {found.isError && <p className="muted small">How often each check is dismissed could not be read just now.</p>}
      {found.data && items.length === 0 && <p className="muted small">No check has found anything yet.</p>}
      {items.length > 0 && (
        <ul className="check-calibration">
          {items.map((item) => (
            <li key={`${item.source}-${item.code}`}>
              <span className="check-calibration-name">{item.words}</span>
              <span className="small">
                {[
                  `Found ${item.found}`,
                  item.dismissed > 0 ? `not a problem ${item.dismissed}` : '',
                  item.accepted > 0 ? `kept on purpose ${item.accepted}` : '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <span className="small muted">{item.note}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Findings({ findings }: { findings: CheckRunFinding[] }) {
  if (findings.length === 0) return null
  return (
    <details className="run-findings">
      <summary>
        Show {findings.length === 1 ? 'the finding' : `the ${findings.length} findings`}
      </summary>
      <ul className="findings">
        {findings.map((finding, index) => (
          <li className={`finding ${finding.source === 'note' ? 'assistant' : finding.severity}`} key={index}>
            <Badge tone={TONE[finding.status]}>{findingStatus(finding)}</Badge>
            <p>{finding.message}</p>
            {finding.quoted && (
              <p className="finding-passage">
                <q>{finding.quoted}</q>
              </p>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}

/**
 * Each check of this scene kept as a record (CON-001), newest first: the checks without
 * the assistant on every saved version, checks with the assistant, and audits that read
 * it. Each says which version it read, whether the scene has changed since, and what
 * became of what it found.
 */
export function CheckHistory({
  planId,
  versions,
  refresh,
}: {
  planId: string
  /** Version id → its number, to say which version a run read. */
  versions: Map<string, number>
  /** Changes when the scene's words or its work change, so the list is read again. */
  refresh: string
}) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'check-runs', planId, refresh)
  const runs = useQuery({ queryKey: key, queryFn: () => api.sceneCheckRuns(scope, planId) })
  const record = useMutation({
    mutationFn: () => api.recordSceneChecks(scope, planId),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'check-runs', planId) }),
  })
  const items = runs.data?.items ?? []

  return (
    <section className="stack tight" aria-labelledby="check-history-heading">
      <div className="card-head">
        <div>
          <h2 id="check-history-heading">Check history</h2>
          <p className="muted small">
            Every check of this scene is kept: what it read, and what became of what it found.
          </p>
        </div>
        {canWrite && (
          <Button size="sm" variant="ghost" disabled={record.isPending} onClick={() => record.mutate()}>
            {record.isPending ? 'Keeping…' : 'Keep a record of the checks now'}
          </Button>
        )}
      </div>
      <ErrorNotice error={record.error} prefix="Could not keep a record" />
      <ErrorNotice error={runs.error} prefix="Could not read the check history" />
      {runs.isPending && <Loading>Reading the check history…</Loading>}
      {runs.data && items.length === 0 && (
        <p className="muted small">
          None yet. Saving the scene keeps a record of the checks without the assistant; checks with
          the assistant and audits are kept too.
        </p>
      )}
      {items.length > 0 && (
        <ol className="rows check-runs">
          {items.map((run) => {
            const part = run.scenes[0]
            const number = part?.version_id ? versions.get(part.version_id) : undefined
            return (
              <li className="row check-run" key={run.id}>
                <div className="grow stack tight">
                  <span className="check-run-title">{runLabel(run)}</span>
                  <span className="audit-summary small">
                    {run.created_at && (
                      <time dateTime={run.created_at} title={fullDate(run.created_at)}>
                        {timeAgo(run.created_at)}
                      </time>
                    )}
                    {part?.draft_id ? <span>A draft</span> : number !== undefined && <span>Version {number}</span>}
                    {run.status !== 'done' && run.status !== 'succeeded' && (
                      <span>{AUDIT_STATUS[run.status] ?? run.status}</span>
                    )}
                    {part && <Tally {...part} />}
                  </span>
                  {part?.stale ? (
                    <span className="small">
                      <Badge tone="neutral">Out of date</Badge> {part.reasons.join(' ')}
                    </span>
                  ) : (
                    <span className="small">
                      <Badge tone="ok">Up to date</Badge>
                    </span>
                  )}
                  <Coverage run={run} />
                  {part && (workText(part) || profileText(run.profile ?? {})) && (
                    <p className="small muted">
                      {[workText(part), profileText(run.profile ?? {})].filter(Boolean).join(' ')}
                    </p>
                  )}
                  <Findings findings={run.findings} />
                </div>
              </li>
            )
          })}
        </ol>
      )}
      <Calibration />
    </section>
  )
}

/**
 * The record an audit keeps (CON-001): for each scene, whether it is out of date now
 * and what became of what the audit found.
 */
export function AuditRecord({ jobId, refresh }: { jobId: string; refresh: string }) {
  const { scope } = useProject()
  const run = useQuery({
    queryKey: scopedKey(scope, 'check-run-job', jobId, refresh),
    queryFn: () => api.checkRunForJob(scope, jobId),
    retry: false,
  })
  if (run.isPending || !run.data) return null
  const found = run.data
  return (
    <section className="stack tight" aria-labelledby="audit-record-heading">
      <h2 id="audit-record-heading" className="audit-group-title">
        Since this audit
      </h2>
      <p className="muted small">
        {found.stale
          ? 'Some scenes have changed since the audit read them, so what it found there may no longer hold.'
          : 'Nothing it read has changed since.'}
      </p>
      <div className="table-wrap">
        <table className="diff-table audit-record">
          <thead>
            <tr>
              <th scope="col">Scene</th>
              <th scope="col">Since</th>
              <th scope="col">Findings</th>
            </tr>
          </thead>
          <tbody>
            {found.scenes.map((part) => (
              <tr key={part.plan_id}>
                <td>{part.title}</td>
                <td data-label="Since">{part.stale ? part.reasons.join(' ') : 'Up to date'}</td>
                <td data-label="Findings">
                  <Tally {...part} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
