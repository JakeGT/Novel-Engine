import { useId, useState, type ReactNode } from 'react'
import type { AssistantNote, DismissAsk, DraftCheck, FindingDismissal } from '../api/client'
import { excerpt, NOTE_KINDS, orderChecks, orderNotes, type Span, type useDismissals } from '../lib/studio'
import { fullDate } from '../lib/time'
import { plural } from '../lib/words'
import { Badge, Button, ErrorNotice } from './ui'

// The checks and the assistant's notes in the studio's margin, each of which the author
// can dismiss with a short reason (CON-010, CON-016). A dismissal belongs to this scene
// and to the words it points at: if those words change, the finding comes back by itself.
// Two kinds of "no", as code checkers have them: kept on purpose ("Intended", their
// "accept") and the check is wrong here ("Not a problem", their "false positive").

export type Point = (start?: number | null, end?: number | null) => Span | null | undefined

type Reason = DismissAsk['reason']

const REASONS: { value: Reason; label: string }[] = [
  { value: 'intended', label: 'Intended' },
  { value: 'not_a_problem', label: 'Not a problem' },
  { value: 'other', label: 'Other reason' },
]

const LASTS: { value: '' | '7' | '30'; label: string }[] = [
  { value: '', label: 'Until the words change' },
  { value: '7', label: 'For a week at most' },
  { value: '30', label: 'For a month at most' },
]

const MAX_REASON = 200

/** "Intended", "Not a problem", or the author's own words in quotation marks. */
function reasonText(dismissal: FindingDismissal): string {
  if (dismissal.reason === 'other') return `“${dismissal.note}”`
  return REASONS.find((item) => item.value === dismissal.reason)?.label ?? 'Dismissed'
}

function lastsText(dismissal: FindingDismissal): string {
  return dismissal.expires_at
    ? `until ${fullDate(dismissal.expires_at)}, or until the words change`
    : 'until the words change'
}

type Dismissals = ReturnType<typeof useDismissals>

/** Where a note points: a quotation and a way to go to it, or why it can no longer be found. */
export function Passage({ text, span, onShow }: { text: string; span: Span | null | undefined; onShow: (span: Span) => void }) {
  if (span === undefined) return null
  if (span === null) return <p className="hint">The passage it was about has changed since.</p>
  return (
    <p className="finding-passage">
      <q>{excerpt(text, span)}</q>{' '}
      <button type="button" className="link-button" onClick={() => onShow(span)}>
        Show in the text
      </button>
    </p>
  )
}

/** The short form under a finding: why it is not a problem, and for how long. */
function DismissForm({
  what,
  anchored,
  busy,
  onDismiss,
  onCancel,
}: {
  what: string
  /** Whether the finding points at words; one about the whole scene has none. */
  anchored: boolean
  busy: boolean
  onDismiss: (reason: Reason, note: string, days: 7 | 30 | null) => void
  onCancel: () => void
}) {
  const id = useId()
  const [reason, setReason] = useState<Reason>('intended')
  const [note, setNote] = useState('')
  const [lasts, setLasts] = useState<'' | '7' | '30'>('')
  const [tried, setTried] = useState(false)
  const missing = reason === 'other' && !note.trim()
  const submit = () => {
    setTried(true)
    if (missing) return
    onDismiss(reason, reason === 'other' ? note.trim() : '', lasts ? (Number(lasts) as 7 | 30) : null)
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
            placeholder="Tobin always talks like this."
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                submit()
              }
            }}
          />
          {tried && missing && (
            <span id={`${id}-missing`} className="field-error" role="alert">
              Say in a few words why this is not a problem.
            </span>
          )}
        </div>
      )}
      <div className="field">
        <label htmlFor={`${id}-lasts`}>How long</label>
        <select id={`${id}-lasts`} value={lasts} onChange={(event) => setLasts(event.target.value as '' | '7' | '30')}>
          {LASTS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <span className="hint">
          {anchored
            ? 'It comes back by itself if the words it points at change.'
            : 'It is about the scene as a whole, so it stays dismissed for this scene.'}
        </span>
      </div>
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

interface Item {
  key: string
  dismissal?: FindingDismissal | null
  message: string
  start?: number | null
  end?: number | null
}

/** One finding with its way to be dismissed, opened in place under it. */
function Finding({
  item,
  className,
  children,
  open,
  onOpen,
  onClose,
  dismissals,
}: {
  item: Item
  className: string
  children: ReactNode
  open: boolean
  onOpen: () => void
  onClose: () => void
  dismissals: Dismissals | null
}) {
  const formId = useId()
  return (
    <li className={className}>
      {children}
      {dismissals && item.key && (
        <>
          {!open && (
            <button
              type="button"
              className="link-button small finding-dismiss"
              aria-expanded={false}
              aria-controls={formId}
              aria-label={`Dismiss: ${item.message}`}
              onClick={onOpen}
            >
              Dismiss…
            </button>
          )}
          <div id={formId} hidden={!open}>
            {open && (
              <DismissForm
                what={item.message}
                anchored={item.start !== null && item.start !== undefined}
                busy={dismissals.dismiss.isPending}
                onCancel={onClose}
                onDismiss={(reason, note, days) =>
                  dismissals.dismiss.mutate({ key: item.key, reason, note, days }, { onSuccess: onClose })
                }
              />
            )}
          </div>
        </>
      )}
    </li>
  )
}

/**
 * What the author dismissed, kept out of the way but one click from view, each with its
 * reason and a way to bring it back.
 */
function Dismissed({
  items,
  noun,
  dismissals,
}: {
  items: Item[]
  noun: [string, string]
  dismissals: Dismissals | null
}) {
  if (items.length === 0) return null
  return (
    <details className="more dismissed">
      <summary>Dismissed: {plural(items.length, noun[0], noun[1])}</summary>
      <ul className="findings">
        {items.map((item) => {
          const dismissal = item.dismissal as FindingDismissal
          return (
            <li key={dismissal.id} className="finding dismissed">
              <p className="muted">{item.message}</p>
              <p className="small">
                <strong>{reasonText(dismissal)}</strong> · {lastsText(dismissal)}
              </p>
              {dismissals && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={dismissals.undo.isPending}
                  aria-label={`Undo dismissing: ${item.message}`}
                  onClick={() => dismissals.undo.mutate(dismissal.id)}
                >
                  Undo
                </Button>
              )}
            </li>
          )
        })}
      </ul>
    </details>
  )
}

/** Said right after a dismissal, with its Undo next to it. */
function JustDismissed({ items, dismissals }: { items: Item[]; dismissals: Dismissals | null }) {
  const key = dismissals?.dismiss.variables?.key
  const found = dismissals?.dismiss.isSuccess && key ? items.find((item) => item.key === key && item.dismissal) : undefined
  if (!found?.dismissal || !dismissals) return null
  const dismissal = found.dismissal
  return (
    <p className="small just-dismissed" role="status">
      {dismissal.reason === 'other' ? 'Dismissed: ' : 'Dismissed as '}
      <strong>{reasonText(dismissal)}</strong>
      {dismissal.reason === 'other' ? ' ' : '. '}
      <button
        type="button"
        className="link-button"
        disabled={dismissals.undo.isPending}
        onClick={() => dismissals.undo.mutate(dismissal.id)}
      >
        Undo
      </button>
    </p>
  )
}

function split<T extends Item>(items: T[]): [T[], T[]] {
  return [items.filter((item) => !item.dismissal), items.filter((item) => item.dismissal)]
}

export function Checks({
  checks,
  text,
  point,
  onShow,
  stale,
  empty,
  dismissals,
}: {
  checks: DraftCheck[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  empty: boolean
  /** Null when the author cannot change this story. */
  dismissals: Dismissals | null
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [active, dismissed] = split(orderChecks(checks).map((check) => ({ ...check, key: check.key ?? '' })))
  return (
    <section className="stack tight" aria-labelledby="checks-heading">
      <div>
        <h2 id="checks-heading" className="studio-side-title">
          Checks
        </h2>
        <p className="hint">Against your story bible and plan, every time the text changes. No assistant needed.</p>
      </div>
      {stale && <p className="hint">These are about the saved text. Save to check again.</p>}
      <JustDismissed items={dismissed} dismissals={dismissals} />
      {/* Before any words, only what the plan and story bible say about the scene (continuity). */}
      {empty && active.length === 0 ? (
        <p className="muted small">Nothing to check until there are some words.</p>
      ) : active.length === 0 ? (
        <p className="muted small">{dismissed.length > 0 ? 'Nothing else to point out.' : 'Nothing to point out.'}</p>
      ) : (
        <ul className="findings">
          {active.map((check, index) => (
            <Finding
              key={`${check.key || check.code}-${index}`}
              item={check}
              className={`finding ${check.severity}`}
              open={open === `${check.key}-${index}`}
              onOpen={() => setOpen(`${check.key}-${index}`)}
              onClose={() => setOpen(null)}
              dismissals={dismissals}
            >
              <Badge tone={check.severity === 'warning' ? 'warn' : 'neutral'}>
                {check.severity === 'warning' ? 'Worth checking' : 'Note'}
              </Badge>
              <p>{check.message}</p>
              <Passage text={text} span={point(check.start, check.end)} onShow={onShow} />
            </Finding>
          ))}
        </ul>
      )}
      <ErrorNotice error={dismissals?.dismiss.error} prefix="Not dismissed" />
      <ErrorNotice error={dismissals?.undo.error} prefix="Not brought back" />
      <Dismissed items={dismissed} noun={['check', 'checks']} dismissals={dismissals} />
    </section>
  )
}

export function Notes({
  notes,
  text,
  point,
  onShow,
  stale,
  action,
  dismissals,
}: {
  notes: AssistantNote[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  action: ReactNode
  dismissals: Dismissals | null
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [active, dismissed] = split(orderNotes(notes).map((note) => ({ ...note, key: note.key ?? '' })))
  return (
    <section className="stack tight" aria-labelledby="notes-heading">
      <div>
        <h2 id="notes-heading" className="studio-side-title">
          The assistant’s notes
        </h2>
        <p className="hint">Every note quotes your words and says which check found it.</p>
      </div>
      {stale && notes.length > 0 && <p className="hint">These are about the saved text.</p>}
      <JustDismissed items={dismissed} dismissals={dismissals} />
      {active.length > 0 && (
        <ul className="findings">
          {active.map((note) => (
            <Finding
              key={note.id}
              item={note}
              className="finding assistant"
              open={open === note.id}
              onOpen={() => setOpen(note.id)}
              onClose={() => setOpen(null)}
              dismissals={dismissals}
            >
              <Badge tone="info">{NOTE_KINDS[note.category] ?? 'Note'}</Badge>
              <p>{note.message}</p>
              {note.suggestion && <p className="small finding-suggestion">Suggestion: {note.suggestion}</p>}
              <Passage text={text} span={point(note.start, note.end)} onShow={onShow} />
            </Finding>
          ))}
        </ul>
      )}
      <ErrorNotice error={dismissals?.dismiss.error} prefix="Not dismissed" />
      <ErrorNotice error={dismissals?.undo.error} prefix="Not brought back" />
      <Dismissed items={dismissed} noun={['note', 'notes']} dismissals={dismissals} />
      {action}
    </section>
  )
}
