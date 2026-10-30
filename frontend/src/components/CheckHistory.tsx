import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CheckPass, type CheckRun, type CheckRunFinding, type CheckRunScene } from '../api/client'
import { findingStatus, passTimingText, profileText, runLabel, secondsText, tallyText, workText } from '../lib/checkRuns'
import { AUDIT_STATUS, PASS_NAMES } from '../lib/studio'
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

/** How one pass was sent, when it could be sent in groups or parts (CON-013). */
interface Sent {
  sent: 'groups' | 'whole'
  why?: string
  groups?: number
  policy?: string
  complete?: boolean
  lines?: string[]
  passages?: { start: number; end: number; checks: string[]; part?: number; words?: string }[]
}

/** Each passage of a pass sent in groups or parts, with its first words and what it got. */
function Passages({ sent }: { sent: Sent }) {
  const passages = sent.passages ?? []
  if (passages.length === 0) return null
  return (
    <ol className="small run-passages">
      {passages.map((passage) => (
        <li key={`${passage.start}-${passage.end}`}>
          {passage.words ? <q>{passage.words}</q> : <span className="muted">A passage</span>}{' '}
          <span className="muted">
            {passage.part ? `part ${passage.part}` : passage.checks.join(', ').toLowerCase()}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** For a check with the assistant: how a long scene was sent, and what each passage got. */
function Coverage({ run }: { run: CheckRun }) {
  const entries = Object.entries((run.coverage ?? {}) as Record<string, Sent>)
  if (entries.length === 0) return null
  return (
    <div className="stack tight run-coverage">
      {entries.map(([name, sent]) => {
        const pass = (name.split(':').pop() ?? name) as CheckPass
        const label = PASS_NAMES[pass] ?? 'A pass'
        const inParts = sent.policy?.startsWith('scene-parts') ?? false
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
              {label}: sent in {sent.groups} {inParts ? 'parts' : 'groups'}.{' '}
              {inParts
                ? 'Every passage was checked once.'
                : sent.complete
                  ? 'Every passage got every check it needs.'
                  : 'Not every passage got every check it needs.'}
            </summary>
            <ul className="small plain-list">
              {(sent.lines ?? []).map((line) => (
                <li key={line}>{line}</li>
              ))}
              {!inParts &&
                [...kinds.entries()].map(([kind, count]) => (
                  <li key={kind}>
                    {kind}: {count === 1 ? '1 passage' : `${count} passages`}
                  </li>
                ))}
            </ul>
            <Passages sent={sent} />
          </details>
        )
      })}
    </div>
  )
}

/** How long each stage and each check took, and each pass with the assistant (CON-001). */
function Timings({ run, part }: { run: CheckRun; part?: CheckRunScene }) {
  const stages = part?.stages ?? []
  // The slowest first: what took the time is what is worth reading.
  const checks = (part?.checks ?? [])
    .filter((check) => check.ms !== null && check.ms !== undefined)
    .sort((a, b) => (b.ms ?? 0) - (a.ms ?? 0))
  const passes = run.timings ?? []
  if (stages.length === 0 && checks.length === 0 && passes.length === 0) return null
  const total = stages.reduce((sum, stage) => sum + stage.ms, 0) + passes.reduce((sum, item) => sum + (item.ms ?? 0), 0)
  return (
    <details className="run-findings">
      <summary>How long it took: {secondsText(total)}</summary>
      <div className="table-wrap">
        <table className="diff-table run-timings">
          <thead>
            <tr>
              <th scope="col">Step</th>
              <th scope="col">How</th>
              <th scope="col">Time</th>
            </tr>
          </thead>
          <tbody>
            {passes.map((item) => (
              <tr key={`${item.plan_id ?? ''}-${item.check_pass}`}>
                <td>{item.words}</td>
                <td data-label="How">{passTimingText(item)}</td>
                <td data-label="Time">{item.ms === null || item.ms === undefined ? '—' : secondsText(item.ms)}</td>
              </tr>
            ))}
            {stages.map((stage) => (
              <tr key={stage.stage}>
                <td>{stage.words}</td>
                <td data-label="How">{stage.stage === 'checks' ? `${checks.length} checks without the assistant` : ''}</td>
                <td data-label="Time">{secondsText(stage.ms)}</td>
              </tr>
            ))}
            {checks.map((check) => (
              <tr key={check.name} className="run-timing-check">
                <td>{check.words}</td>
                <td data-label="How">{check.reused ? 'Reused: nothing it reads had changed' : 'Worked out again'}</td>
                <td data-label="Time">{secondsText(check.ms ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** The pictures a check against the scene's pictures was given, and what changed since (CON-001). */
function PicturesRead({ part }: { part: CheckRunScene }) {
  const pictures = part.pictures ?? []
  if (pictures.length === 0) return null
  return (
    <div className="stack tight run-pictures">
      <p className="small">
        Compared with {pictures.length === 1 ? 'one picture' : `${pictures.length} pictures`}:
      </p>
      <ul className="small plain-list">
        {pictures.map((picture) => (
          <li key={picture.asset_id}>
            {picture.label} ({picture.choice === 'send' ? 'the picture itself' : 'its description'})
            {picture.changed ? <span className="muted"> — {picture.changed}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Whether a scene of a run is out of date, counting its pictures, and why. */
function sinceWords(part: CheckRunScene): { stale: boolean; words: string } {
  const changed = (part.pictures ?? []).filter((picture) => picture.changed)
  const pictureWords =
    changed.length === 0 ? '' : changed.length === 1 ? 'A picture it read has changed.' : 'Pictures it read have changed.'
  return {
    stale: part.stale || changed.length > 0,
    words: [part.stale ? part.reasons.join(' ') : '', pictureWords].filter(Boolean).join(' '),
  }
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

/** One kind of finding dismissed wider than one scene, each with what it applies to (CON-016). */
function WiderDismissals() {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'scoped-dismissals')
  const found = useQuery({ queryKey: key, queryFn: () => api.scopedDismissals(scope) })
  const withdraw = useMutation({
    mutationFn: (dismissalId: string) => api.withdrawScopedDismissal(scope, dismissalId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'studio') })
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'check-runs') })
    },
  })
  // One that covers nothing now (a scene it names left the plan) is shown too, so it
  // can be undone; it says so in its own words.
  const standing = (found.data?.items ?? []).filter((item) => item.standing || item.lapsed)
  if (standing.length === 0) return null
  return (
    <section className="stack tight" aria-labelledby="wider-dismissals-heading">
      <h3 id="wider-dismissals-heading" className="check-calibration-title">
        Dismissed beyond one scene
      </h3>
      <ul className="check-calibration">
        {standing.map((item) => (
          <li key={item.id}>
            <span className="check-calibration-name">{item.applies_to}</span>
            <span className="small muted">
              {item.reason === 'other'
                ? `“${item.note}”`
                : item.reason === 'intended'
                  ? 'Intended'
                  : 'Not a problem'}
              {item.lapsed
                ? ' · no longer applies'
                : item.expires_at
                  ? ` · until ${fullDate(item.expires_at)}`
                  : ' · until you undo it'}
            </span>
            {canWrite && (
              <Button
                size="sm"
                variant="ghost"
                disabled={withdraw.isPending}
                aria-label={`Undo everywhere: ${item.applies_to}`}
                onClick={() => withdraw.mutate(item.id)}
              >
                Undo everywhere
              </Button>
            )}
          </li>
        ))}
      </ul>
      <ErrorNotice error={withdraw.error} prefix="Not brought back" />
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
            {finding.picture?.changed && (
              <p className="small muted">
                The picture it was compared with has changed since: {finding.picture.changed}
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
            const since = part ? sinceWords(part) : { stale: false, words: '' }
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
                  {since.stale ? (
                    <span className="small">
                      <Badge tone="neutral">Out of date</Badge> {since.words}
                    </span>
                  ) : (
                    <span className="small">
                      <Badge tone="ok">Up to date</Badge>
                    </span>
                  )}
                  <Coverage run={run} />
                  <Timings run={run} part={part} />
                  {part && <PicturesRead part={part} />}
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
      <WiderDismissals />
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
