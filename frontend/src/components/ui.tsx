import { useId, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, isApiError, type Scope } from '../api/client'
import type { CanonWrite } from '../project'

type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

const MUTATION_STATES: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  validating: { label: 'Draft · validating', tone: 'neutral' },
  needs_review: { label: 'Proposed · awaiting review', tone: 'warn' },
  approved: { label: 'Approved · not yet canon', tone: 'info' },
  committing: { label: 'Recovery pending', tone: 'danger' },
  recovery_required: { label: 'Recovery pending', tone: 'danger' },
  committed: { label: 'Canonical', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'danger' },
}

/** One vocabulary for record state everywhere: draft, proposed, canonical, retired, recovery. */
export function MutationStateBadge({ status }: { status: string }) {
  const state = MUTATION_STATES[status] ?? { label: status, tone: 'neutral' as Tone }
  return <Badge tone={state.tone}>{state.label}</Badge>
}

export function LifecycleBadge({ retired }: { retired: boolean }) {
  return retired ? <Badge tone="danger">Retired</Badge> : <Badge tone="ok">Canonical</Badge>
}

export function ErrorNotice({ error, prefix }: { error: unknown; prefix?: string }) {
  if (!error) return null
  return (
    <p className="error" role="alert">
      {prefix ? `${prefix}: ` : ''}
      {errorMessage(error)}
    </p>
  )
}

export function ReadOnlyNotice({ canWrite }: { canWrite: boolean }) {
  if (canWrite) return null
  return (
    <p className="notice" role="note">
      You have read-only access to this project, so editing is turned off. Ask a workspace owner for
      the editor role to make changes.
    </p>
  )
}

/**
 * The outcome of a canon write that did not succeed, with its recovery path:
 * resubmit against the new head, go review the parked proposal, or fix the input.
 */
export function WriteProblem<T, A>({
  write,
  scope,
}: {
  write: CanonWrite<T, A>
  scope: Scope
}) {
  const { error } = write
  if (!error) return null
  if (write.stale) {
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>Canon changed while you were editing.</strong> Nothing was saved, and everything
          you entered is still here.
        </p>
        <button
          type="button"
          className="button"
          onClick={() => void write.retryWithFreshHead()}
          disabled={write.pending}
        >
          Reload head and resubmit
        </button>
      </div>
    )
  }
  if (isApiError(error, 'review_required')) {
    const mutationId = error.fieldErrors.mutation_id
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>This change needs review before it can become canon.</strong> It was saved as a
          proposal, not committed.
          {error.fieldErrors.reasons ? ` Reason: ${error.fieldErrors.reasons.replace(/,/g, ', ')}.` : ''}
        </p>
        {mutationId && (
          <Link to={`/projects/${scope.projectId}/mutations/${mutationId}`}>
            Open the proposal for review
          </Link>
        )}
      </div>
    )
  }
  return <ErrorNotice error={error} prefix="Not saved" />
}

interface FieldProps {
  label: string
  hint?: string
  error?: string | null
  children: (props: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }) => ReactNode
}

/** A labelled control whose hint and error are announced with it. */
export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ')
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({
        id,
        'aria-describedby': describedBy || undefined,
        'aria-invalid': error ? true : undefined,
      })}
      {hint && (
        <span id={`${id}-hint`} className="muted">
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

export function Json({ value }: { value: unknown }) {
  return <pre tabIndex={0}>{JSON.stringify(value ?? null, null, 2)}</pre>
}
