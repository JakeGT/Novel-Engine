import { useId, useState } from 'react'
import type { MutationValidation, ReviewDismissAsk, ReviewFinding } from '../api/client'
import type { ReviewFindings } from '../lib/reviewFindings'
import { Button, ErrorNotice } from './ui'

// Advisory findings on a proposal in review can be dismissed with a reason, for this
// proposal only (CON-016). A dismissal holds while the change and what the finding says
// stay the same: edit the change, or let the story change what it says, and it comes
// back. A finding that must be fixed first is never dismissed.

type Issue = MutationValidation['issues'][number]
type Reason = ReviewDismissAsk['reason']

const REASONS: { value: Reason; label: string }[] = [
  { value: 'intended', label: 'Intended' },
  { value: 'not_a_problem', label: 'Not a problem' },
  { value: 'other', label: 'Other reason' },
]
const MAX_REASON = 200

function reasonText(finding: ReviewFinding): string {
  const dismissal = finding.dismissal
  if (!dismissal) return ''
  if (dismissal.reason === 'other') return `“${dismissal.note}”`
  return REASONS.find((item) => item.value === dismissal.reason)?.label ?? 'Dismissed'
}

function DismissForm({
  what,
  busy,
  onDismiss,
  onCancel,
}: {
  what: string
  busy: boolean
  onDismiss: (reason: Reason, note: string) => void
  onCancel: () => void
}) {
  const id = useId()
  const [reason, setReason] = useState<Reason>('intended')
  const [note, setNote] = useState('')
  const [tried, setTried] = useState(false)
  const missing = reason === 'other' && !note.trim()
  const submit = () => {
    setTried(true)
    if (!missing) onDismiss(reason, reason === 'other' ? note.trim() : '')
  }
  return (
    <div className="dismiss-form stack tight" role="group" aria-label={`Dismiss: ${what}`}>
      <div className="segmented" role="group" aria-label="Why it is not a problem">
        {REASONS.map((item) => (
          <button key={item.value} type="button" aria-pressed={reason === item.value} onClick={() => setReason(item.value)}>
            {item.label}
          </button>
        ))}
      </div>
      {reason === 'other' && (
        <div className="field">
          <label htmlFor={`${id}-note`}>Your reason</label>
          <input
            id={`${id}-note`}
            value={note}
            maxLength={MAX_REASON}
            autoFocus
            aria-invalid={tried && missing ? true : undefined}
            aria-describedby={tried && missing ? `${id}-missing` : undefined}
            placeholder="The guild and the harbour are two different things."
            onChange={(event) => setNote(event.target.value)}
          />
          {tried && missing && (
            <span id={`${id}-missing`} className="field-error" role="alert">
              Say in a few words why this is not a problem.
            </span>
          )}
        </div>
      )}
      <p className="hint">For these changes only. It comes back if you edit this change, or if what it says changes.</p>
      <div className="actions">
        <Button size="sm" variant="primary" disabled={busy} onClick={submit}>
          {busy ? 'Dismissing…' : 'Dismiss'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

/** One finding on a change: must be fixed (never dismissed), worth a look, or dismissed. */
function IssueNotice({
  issue,
  reviewed,
  editable,
}: {
  issue: Issue
  reviewed: ReviewFindings | null
  editable: boolean
}) {
  const [open, setOpen] = useState(false)
  const formId = useId()
  const blocking = issue.severity === 'blocking'
  const finding = !blocking ? reviewed?.find(issue) : undefined
  if (finding?.dismissal) {
    return (
      <div className="notice plain review-dismissed" role="note">
        <p className="muted">
          <strong>Dismissed ({reasonText(finding)}):</strong> {issue.message}
        </p>
        {editable && reviewed && (
          <Button
            size="sm"
            variant="ghost"
            disabled={reviewed.undo.isPending}
            aria-label={`Bring back: ${issue.message}`}
            onClick={() => reviewed.undo.mutate(finding.dismissal!.id)}
          >
            Bring back
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className={`notice ${blocking ? 'danger' : 'plain'}`} role={blocking ? 'alert' : 'note'}>
      <p>
        <strong>{blocking ? 'This must be fixed first' : 'Worth a look'}:</strong> {issue.message}
      </p>
      {finding?.came_back && (
        <p className="hint">Back again: this change or what it says has changed since you dismissed it.</p>
      )}
      {finding && reviewed?.calibration(issue) && (
        <p className="hint review-calibration">How often you dismiss this kind in this story: {reviewed.calibration(issue)}</p>
      )}
      {editable && reviewed && finding && !open && (
        <button
          type="button"
          className="link-button small"
          aria-expanded={false}
          aria-controls={formId}
          aria-label={`Dismiss: ${issue.message}`}
          onClick={() => setOpen(true)}
        >
          Dismiss…
        </button>
      )}
      <div id={formId} hidden={!open}>
        {open && reviewed && finding && (
          <DismissForm
            what={issue.message}
            busy={reviewed.dismiss.isPending}
            onCancel={() => setOpen(false)}
            onDismiss={(reason, note) =>
              reviewed.dismiss.mutate({ key: finding.key, reason, note }, { onSuccess: () => setOpen(false) })
            }
          />
        )}
      </div>
    </div>
  )
}

/** The findings on one change, each where the author decides about that change. */
export function ChangeIssues({
  issues,
  reviewed,
  editable,
}: {
  issues: Issue[]
  reviewed: ReviewFindings | null
  editable: boolean
}) {
  if (issues.length === 0) return null
  return (
    <>
      {issues.map((issue, index) => (
        <IssueNotice key={`${issue.code}-${index}`} issue={issue} reviewed={reviewed} editable={editable} />
      ))}
      <ErrorNotice error={reviewed?.dismiss.error} prefix="Not dismissed" />
      <ErrorNotice error={reviewed?.undo.error} prefix="Not brought back" />
    </>
  )
}
