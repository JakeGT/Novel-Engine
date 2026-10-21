import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, isApiError, type AuditSchedule, type CheckPass, type PlanItem } from '../api/client'
import {
  AUDIT_STATUS,
  auditPath,
  chosenTokens,
  keepChosenPasses,
  newRequestKey,
  readChosenPasses,
  tokenCount,
} from '../lib/studio'
import { fullDate } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { PassChoices } from './CheckPasses'
import { Button, Dialog, ErrorNotice, Loading } from './ui'

/**
 * "Audit this part of the story" (CON-011): the scenes in a part of the plan, what each
 * check would cost across them, and a start. The checks without the assistant always run,
 * free; the audit itself runs in the background and its results have their own page.
 */
export function AuditDialog({ item, onClose }: { item: PlanItem; onClose: () => void }) {
  const { scope } = useProject()
  const navigate = useNavigate()
  const [passes, setPasses] = useState(() => readChosenPasses(scope))
  const requestKey = useRef(newRequestKey())
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'audit-estimate', item.id),
    queryFn: () => api.auditEstimate(scope, item.id),
  })
  const earlier = useQuery({
    queryKey: scopedKey(scope, 'audits', item.id),
    queryFn: () => api.audits(scope, item.id),
  })
  const start = useMutation({
    mutationFn: () => api.startAudit(scope, item.id, passes, requestKey.current),
    onSuccess: (job) => {
      keepChosenPasses(scope, passes)
      navigate(auditPath(scope.projectId, job.id))
    },
  })

  const found = estimate.data
  const written = found?.written ?? 0
  const tooMany = found ? written > found.max_scenes : false
  const cost = chosenTokens(found?.passes, passes)
  const blocked = !found
    ? 'Working out what is written…'
    : written === 0
      ? 'Nothing in this part of the story is written yet, so there is nothing to audit.'
      : tooMany
        ? `An audit reads at most ${found.max_scenes} written scenes; this part has ${written}. Audit a smaller part of it.`
        : null

  return (
    <Dialog title={`Audit “${item.title}”`} onClose={onClose} wide>
      <div className="stack">
        <p>
          Checks every written scene in this part of the story, in the order a reader meets them.
          Each note quotes the scene and links to the passage. Nothing in your story is changed.
        </p>
        {estimate.isPending && <Loading>Working out what is written…</Loading>}
        <ErrorNotice error={estimate.error} prefix="Could not work out what an audit would read" />
        {found && (
          <p className="audit-summary" role="status">
            <span>
              <strong>{plural(written, 'written scene')}</strong> to audit
            </span>
            {found.scenes.length > written && (
              <span>{plural(found.scenes.length - written, 'scene')} not written yet, so left out</span>
            )}
          </p>
        )}
        <PassChoices
          passes={found?.passes}
          chosen={passes}
          onChange={setPasses}
          legend="What the assistant checks in each scene"
          hint="Costs are for every written scene in this part. The checks without the assistant always run, free."
        />
        {found && !blocked && (
          <p className="hint" id="audit-cost">
            {passes.length === 0
              ? 'Only the checks without the assistant will run. Nothing is spent.'
              : cost !== null
                ? `About ${tokenCount(cost)} tokens in all. If that is more than you asked to be asked about, you will be asked first.`
                : ''}
          </p>
        )}
        {blocked && found && (
          <p className="hint" id="audit-blocked">
            {blocked}
          </p>
        )}
        {start.error && isApiError(start.error, 'not_confirmed') ? (
          <p className="hint" role="status">
            Not started, as you chose. Nothing was spent.
          </p>
        ) : (
          <ErrorNotice error={start.error} prefix="Could not start the audit" />
        )}
        <div className="actions">
          <Button
            variant="primary"
            icon="spark"
            disabled={blocked !== null || start.isPending}
            // Only an element on the page: neither is shown until the estimate is in.
            aria-describedby={!found ? undefined : blocked ? 'audit-blocked' : 'audit-cost'}
            onClick={() => start.mutate()}
          >
            {start.isPending ? 'Starting…' : 'Start the audit'}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
        {found && !blocked && <ScheduleSection item={item} passes={passes} cost={cost} />}
        {earlier.data && earlier.data.items.length > 0 && (
          <section className="stack tight" aria-labelledby="earlier-audits">
            <h3 id="earlier-audits" className="audit-group-title">
              Earlier audits of this part
            </h3>
            <ul className="rows">
              {earlier.data.items.map((job) => (
                <li className="row" key={job.id}>
                  <Link to={auditPath(scope.projectId, job.id)}>Audit of {fullDate(job.created_at)}</Link>
                  <span className="muted small">{AUDIT_STATUS[job.status] ?? job.status}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Dialog>
  )
}

const EVERY: { days: number; label: string }[] = [
  { days: 1, label: 'Every day' },
  { days: 3, label: 'Every 3 days' },
  { days: 7, label: 'Every week' },
  { days: 14, label: 'Every 2 weeks' },
  { days: 30, label: 'Every 30 days' },
]

function suggestedMost(cost: number | null): number {
  if (!cost) return 0
  // Room for the scenes to grow a little before a run would stop for being too big.
  return Math.ceil((cost * 1.5) / 1000) * 1000
}

/**
 * "Run this audit on a schedule" (CON-011): the same audit every few days, as you, using
 * at most what you set each time. A run with nothing changed since the last one is
 * skipped and spends nothing; each run is checked against your limits like any other.
 */
function ScheduleSection({ item, passes, cost }: { item: PlanItem; passes: CheckPass[]; cost: number | null }) {
  const { scope, canWrite } = useProject()
  const schedules = useQuery({ queryKey: scopedKey(scope, 'audit-schedules'), queryFn: () => api.auditSchedules(scope) })
  if (!canWrite || !schedules.data) return null
  const current = schedules.data.items.find((entry) => entry.plan_id === item.id)
  return <ScheduleForm item={item} passes={passes} cost={cost} current={current} />
}

function ScheduleForm({
  item,
  passes,
  cost,
  current,
}: {
  item: PlanItem
  passes: CheckPass[]
  cost: number | null
  current: AuditSchedule | undefined
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const id = useId()
  const key = scopedKey(scope, 'audit-schedules')
  const [every, setEvery] = useState<number>(current?.every_days ?? 7)
  const [most, setMost] = useState<string>(() => String(current?.max_tokens || suggestedMost(cost)))
  const mostValue = Number(most)
  const free = passes.length === 0
  const tooSmall = !free && cost !== null && mostValue < cost
  const invalid = !free && (!Number.isFinite(mostValue) || mostValue <= 0)
  const save = useMutation({
    mutationFn: () =>
      api.saveAuditSchedule(scope, {
        plan_id: item.id,
        passes,
        every_days: every,
        max_tokens: free ? 0 : Math.round(mostValue),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })
  const stop = useMutation({
    mutationFn: (scheduleId: string) => api.removeAuditSchedule(scope, scheduleId),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })
  return (
    <details className="more">
      <summary>
        Run this audit on a schedule
        {current && <span className="muted"> · {EVERY.find((e) => e.days === current.every_days)?.label.toLowerCase() ?? `every ${current.every_days} days`}</span>}
      </summary>
      <div className="stack tight">
        <p className="hint">
          The same audit, with the checks chosen above, every few days as you. A run with nothing
          changed since the last one is skipped and spends nothing.
        </p>
        {current && (
          <p className="small" role="status">
            {current.enabled ? `Next run ${fullDate(current.next_run_at)}.` : 'Turned off.'}{' '}
            {current.last_outcome && `Last time: ${current.last_outcome}`}{' '}
            {current.last_job_id && (
              <Link to={auditPath(scope.projectId, current.last_job_id)}>Open the last audit</Link>
            )}
          </p>
        )}
        <div className="schedule-form">
          <div className="field">
            <label htmlFor={`${id}-every`}>How often</label>
            <select id={`${id}-every`} value={every} onChange={(event) => setEvery(Number(event.target.value))}>
              {EVERY.map((entry) => (
                <option key={entry.days} value={entry.days}>
                  {entry.label}
                </option>
              ))}
            </select>
          </div>
          {!free && (
            <div className="field">
              <label htmlFor={`${id}-most`}>At most this many tokens each time</label>
              <input
                id={`${id}-most`}
                type="number"
                inputMode="numeric"
                min={1}
                step={1000}
                value={most}
                aria-invalid={invalid || tooSmall ? true : undefined}
                aria-describedby={`${id}-most-hint`}
                onChange={(event) => setMost(event.target.value)}
              />
              <span id={`${id}-most-hint`} className={tooSmall || invalid ? 'field-error' : 'hint'}>
                {invalid
                  ? 'Say the most each run may use.'
                  : tooSmall
                    ? `An audit now would use about ${tokenCount(cost ?? 0)} tokens, so it would not run.`
                    : 'A run that would use more does not start, and says so here.'}
              </span>
            </div>
          )}
        </div>
        {free && <p className="hint">On a schedule, too, only the checks without the assistant run. Nothing is spent.</p>}
        {save.error && isApiError(save.error, 'not_confirmed') ? (
          <p className="hint" role="status">
            Not set up, as you chose.
          </p>
        ) : (
          <ErrorNotice error={save.error} prefix="The schedule was not saved" />
        )}
        <div className="actions">
          <Button disabled={save.isPending || invalid} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : current ? 'Change the schedule' : 'Run it on this schedule'}
          </Button>
        </div>
        {save.data && (
          <p className="small" role="status">
            Scheduled. The first run is {fullDate(save.data.next_run_at)}.
          </p>
        )}
        {current && (
          <div className="actions schedule-stop">
            <Button size="sm" variant="danger-quiet" disabled={stop.isPending} onClick={() => stop.mutate(current.id)}>
              {stop.isPending ? 'Stopping…' : 'Stop the schedule'}
            </Button>
            <span className="hint">Audits it already ran are kept.</span>
          </div>
        )}
        <ErrorNotice error={stop.error} prefix="The schedule was not stopped" />
      </div>
    </details>
  )
}
