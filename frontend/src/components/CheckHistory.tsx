import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CheckRunFinding } from '../api/client'
import { findingStatus, runLabel } from '../lib/checkRuns'
import { AUDIT_STATUS } from '../lib/studio'
import { fullDate, timeAgo } from '../lib/time'
import { scopedKey, useProject } from '../project'
import { Badge, Button, ErrorNotice, Loading } from './ui'

const TONE: Record<string, 'ok' | 'neutral' | 'warn'> = { open: 'warn', dismissed: 'neutral', fixed: 'ok' }

function Tally({ open, dismissed, fixed }: { open: number; dismissed: number; fixed: number }) {
  if (open + dismissed + fixed === 0) return <span>Nothing found</span>
  return (
    <span>
      {open} open · {dismissed} dismissed · {fixed} fixed
    </span>
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
                    {part && <Tally open={part.open} dismissed={part.dismissed} fixed={part.fixed} />}
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
                  <Findings findings={run.findings} />
                </div>
              </li>
            )
          })}
        </ol>
      )}
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
                  <Tally open={part.open} dismissed={part.dismissed} fixed={part.fixed} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
