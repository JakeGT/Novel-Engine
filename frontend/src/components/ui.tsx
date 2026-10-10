import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, isApiError, type Scope } from '../api/client'
import { CHANGE_STATES, REVIEW_REASONS, initial, typeLook, type Tone } from '../lib/words'
import type { CanonWrite } from '../project'
import { Icon, type IconName } from './icons'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone === 'neutral' ? '' : tone}`}>{children}</span>
}

/** One vocabulary for a change's state everywhere it appears. */
export function ChangeStateBadge({ status }: { status: string }) {
  const state = CHANGE_STATES[status] ?? { label: status, tone: 'neutral' as Tone }
  return <Badge tone={state.tone}>{state.label}</Badge>
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'ghost' | 'danger' | 'danger-quiet'
  size?: 'sm' | 'md' | 'lg'
  icon?: IconName
}

export function Button({
  variant = 'default',
  size = 'md',
  icon,
  className = '',
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = ['btn', variant === 'default' ? '' : variant, size === 'md' ? '' : size, className]
  return (
    <button type={type} className={classes.filter(Boolean).join(' ')} {...rest}>
      {icon && <Icon name={icon} size={size === 'sm' ? 15 : 17} />}
      {children}
    </button>
  )
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return (
    <p className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {children}
    </p>
  )
}

export function ErrorNotice({ error, prefix }: { error: unknown; prefix?: string }) {
  if (!error) return null
  return (
    <p className="notice danger" role="alert">
      <span>
        {prefix ? <strong>{prefix}. </strong> : null}
        {errorMessage(error)}
      </span>
    </p>
  )
}

export function ReadOnlyNotice({ canWrite }: { canWrite: boolean }) {
  if (canWrite) return null
  return (
    <p className="notice plain" role="note">
      You can read this project but not change it. Ask a workspace owner to make you an editor.
    </p>
  )
}

export function EmptyState({
  icon = 'feather',
  title,
  children,
  action,
}: {
  icon?: IconName
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name={icon} size={24} />
      </span>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

export function TypeAvatar({
  type,
  name,
  size,
}: {
  type: string
  name?: string
  size?: 'sm' | 'lg'
}) {
  const look = typeLook(type)
  return (
    <span className={`avatar tint-${look.tint} ${size ?? ''}`} aria-hidden="true">
      {name ? initial(name) : <Icon name={look.icon} size={size === 'lg' ? 28 : size === 'sm' ? 14 : 18} />}
    </span>
  )
}

/**
 * The outcome of a canon write that did not succeed, with its way forward:
 * resend against the latest version, go review the parked change, or fix the input.
 */
export function WriteProblem<T, A>({ write, scope }: { write: CanonWrite<T, A>; scope: Scope }) {
  const { error } = write
  if (!error) return null
  if (write.stale) {
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>Someone else changed the story while you were editing.</strong> Nothing was saved,
          and everything you entered is still here.
        </p>
        <Button onClick={() => void write.retryWithFreshHead()} disabled={write.pending}>
          Save on top of the latest version
        </Button>
      </div>
    )
  }
  if (isApiError(error, 'review_required')) {
    const mutationId = error.fieldErrors.mutation_id
    const reasons = (error.fieldErrors.reasons ?? '')
      .split(',')
      .filter(Boolean)
      .map((reason) => REVIEW_REASONS[reason] ?? reason)
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>This change needs a review before it becomes canon.</strong> It has been saved as
          a suggested change, not applied.
          {reasons.length > 0 ? ` It needs review because ${reasons.join(' and ')}.` : ''}
        </p>
        {mutationId && (
          <Link className="btn" to={`/projects/${scope.projectId}/review/${mutationId}`}>
            Review the change
          </Link>
        )}
      </div>
    )
  }
  if (isApiError(error) && /published schema/i.test(error.message)) {
    return (
      <div className="notice warn" role="alert">
        <p>
          <strong>This project is not set up yet.</strong> It needs its list of entry types
          published once before anything can be saved.
        </p>
        <Link className="btn" to={`/projects/${scope.projectId}/customize`}>
          Finish setting up
        </Link>
      </div>
    )
  }
  return <ErrorNotice error={error} prefix="Not saved" />
}

interface FieldProps {
  label: string
  hint?: ReactNode
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
        <span id={`${id}-hint`} className="hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className="field-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

export function Json({ value }: { value: unknown }) {
  return <pre tabIndex={0}>{JSON.stringify(value ?? null, null, 2)}</pre>
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * A modal dialog: focus moves in when it opens, stays inside while it is open,
 * Escape and the backdrop close it, and focus returns to where it came from.
 */
export function Dialog({
  title,
  onClose,
  children,
  wide = false,
  className,
  labelledBy,
}: {
  title?: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
  className?: string
  labelledBy?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  }, [onClose])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const node = ref.current
    const first = node?.querySelector<HTMLElement>('[data-autofocus]') ?? node?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? node)?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close.current()
        return
      }
      if (event.key !== 'Tab' || !node) return
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) return
      const start = items[0]
      const end = items[items.length - 1]
      if (event.shiftKey && document.activeElement === start) {
        event.preventDefault()
        end.focus()
      } else if (!event.shiftKey && document.activeElement === end) {
        event.preventDefault()
        start.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = overflow
      previous?.focus?.()
    }
  }, [])

  return (
    <div
      className="backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={ref}
        className={className ?? `dialog ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy ?? (title ? titleId : undefined)}
        tabIndex={-1}
      >
        {title && (
          <div className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="btn ghost icon" aria-label="Close" onClick={onClose}>
              <Icon name="x" />
            </button>
          </div>
        )}
        {title ? <div className="dialog-body">{children}</div> : children}
      </div>
    </div>
  )
}
