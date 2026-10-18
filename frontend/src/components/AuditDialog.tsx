import { useMutation, useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, isApiError, type PlanItem } from '../api/client'
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
