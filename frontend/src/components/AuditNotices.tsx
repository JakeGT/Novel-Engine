import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type AuthorNotice } from '../api/client'
import { auditPath } from '../lib/studio'
import { fullDate } from '../lib/time'
import { scopedKey, useProject } from '../project'
import { Badge, Button, ErrorNotice } from './ui'

const STATUS: Record<AuthorNotice['status'], { label: string; tone: 'ok' | 'warn' | 'neutral' }> = {
  succeeded: { label: 'Finished', tone: 'ok' },
  failed: { label: 'Stopped by a problem', tone: 'warn' },
  cancelled: { label: 'Stopped', tone: 'neutral' },
}

/**
 * Notices for audits the author scheduled (CON-011): what each found and what it used,
 * until marked read. On the story's home page, the ones not yet read; on an audit's own
 * page (``jobId``), that audit's notice, read or not.
 */
export function AuditNotices({ jobId }: { jobId?: string }) {
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'notices', jobId ?? 'unseen')
  const notices = useQuery({
    queryKey: key,
    queryFn: () => api.notices(scope, jobId ? { jobId } : { unseen: true }),
  })
  const seen = useMutation({
    mutationFn: (noticeId: string) => api.markNoticeSeen(scope, noticeId),
    onSuccess: () => client.invalidateQueries({ queryKey: scopedKey(scope, 'notices') }),
  })
  const items = notices.data?.items ?? []
  if (items.length === 0) return null
  const headingId = jobId ? 'audit-notice-heading' : 'notices-heading'
  return (
    <section className="card stack tight audit-notices" aria-labelledby={headingId}>
      <h2 id={headingId}>{jobId ? 'Your scheduled audit' : 'Audits you scheduled'}</h2>
      <ul className="rows">
        {items.map((item) => {
          const status = STATUS[item.status]
          return (
            <li className="row stack tight" key={item.id}>
              <span className="actions">
                <Badge tone={status.tone}>{status.label}</Badge>
                {item.created_at && <span className="muted small">{fullDate(item.created_at)}</span>}
                {item.what_if && <span className="muted small">In the what-if “{item.what_if}”</span>}
                {item.seen && <span className="muted small">Read</span>}
              </span>
              <p>{item.sentence}</p>
              <span className="actions">
                {!jobId && (
                  <Link className="btn sm" to={auditPath(scope.projectId, item.job_id)}>
                    Open the audit
                  </Link>
                )}
                {!item.seen && (
                  <Button size="sm" variant="ghost" disabled={seen.isPending} onClick={() => seen.mutate(item.id)}>
                    Mark as read
                  </Button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      <ErrorNotice error={seen.error} prefix="Could not mark it as read" />
    </section>
  )
}
