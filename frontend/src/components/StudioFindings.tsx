import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  isApiError,
  type AssistantNote,
  type DismissAsk,
  type DraftCheck,
  type FindingDismissal,
  type FindingRef,
  type SceneVoice,
} from '../api/client'
import { excerpt, NOTE_KINDS, orderChecks, orderNotes, tokenCount, type Span, type useDismissals } from '../lib/studio'
import { fullDate } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Badge, Button, ErrorNotice, Loading } from './ui'

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
            ? 'It comes back by itself if the words it points at, or what it says, change.'
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
  proposal,
}: {
  item: Item
  className: string
  children: ReactNode
  open: boolean
  onOpen: () => void
  onClose: () => void
  dismissals: Dismissals | null
  /** The scene and the finding, when it can be settled in the story bible (CON-010). */
  proposal?: { planId: string; ref: FindingRef } | null
}) {
  const formId = useId()
  return (
    <li className={className}>
      {children}
      {proposal && <SettleInBible planId={proposal.planId} findingRef={proposal.ref} anchored={item.start != null} />}
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
  planId = null,
}: {
  checks: DraftCheck[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  empty: boolean
  /** Null when the author cannot change this story. */
  dismissals: Dismissals | null
  /** The scene, when these are about its saved words and can be settled in the story bible. */
  planId?: string | null
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
              proposal={planId && check.key && !stale ? { planId, ref: { kind: 'check', key: check.key } } : null}
            >
              <Badge tone={check.severity === 'warning' ? 'warn' : 'neutral'}>
                {check.severity === 'warning' ? 'Worth checking' : 'Note'}
              </Badge>
              <p>{check.message}</p>
              {check.came_back && (
                <p className="hint">
                  Back again: what it says has changed since you dismissed it, so it is worth another look.
                </p>
              )}
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
  planId = null,
}: {
  notes: AssistantNote[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  action: ReactNode
  dismissals: Dismissals | null
  /** The scene, when these are about its saved words and can be settled in the story bible. */
  planId?: string | null
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
              proposal={planId && !stale ? { planId, ref: { kind: 'note', key: note.id } } : null}
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

// --- settling a finding in the story bible (CON-010) ---------------------------------

type Choice = 'make_true' | 'change_bible'

/**
 * "Settle it in the story bible…": instead of changing the words, the author has the
 * assistant draft a change to the story bible, which waits in review. The cost is shown
 * before anything is sent, and the words are never rewritten.
 */
function SettleInBible({ planId, findingRef, anchored }: { planId: string; findingRef: FindingRef; anchored: boolean }) {
  const { scope } = useProject()
  const id = useId()
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState<Choice>(anchored ? 'make_true' : 'change_bible')
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'finding-proposal-estimate', planId, findingRef.kind, findingRef.key),
    queryFn: () => api.findingProposalEstimate(scope, planId, findingRef),
    enabled: open,
    retry: false,
  })
  const draft = useMutation({ mutationFn: () => api.findingProposal(scope, planId, findingRef, choice) })
  const passage = estimate.data ? estimate.data.has_passage : anchored
  if (!open) {
    return (
      <button
        type="button"
        className="link-button small finding-settle"
        aria-expanded={false}
        aria-controls={id}
        onClick={() => setOpen(true)}
      >
        Settle it in the story bible…
      </button>
    )
  }
  const result = draft.data
  return (
    <div id={id} className="settle-form stack tight" role="group" aria-label="Settle it in the story bible">
      <p className="small">
        The assistant drafts a change to your story bible for you to review. Your words are not changed.
      </p>
      <div className="segmented" role="group" aria-label="What to change">
        <button
          type="button"
          aria-pressed={choice === 'make_true'}
          disabled={!passage}
          onClick={() => setChoice('make_true')}
        >
          Make this true in the story
        </button>
        <button type="button" aria-pressed={choice === 'change_bible'} onClick={() => setChoice('change_bible')}>
          Change the story bible instead
        </button>
      </div>
      <p className="hint" id={`${id}-cost`}>
        {choice === 'make_true'
          ? 'Adds what the passage says, and leaves everything else as it is.'
          : 'Ends or changes what the story bible says here, so it agrees with your words.'}{' '}
        {!passage && 'This one is about the scene as a whole, so there is no passage to make true. '}
        {estimate.data && `About ${tokenCount(estimate.data.total_tokens)} tokens.`}
      </p>
      {estimate.isPending && <Loading>Working out what it would use…</Loading>}
      <ErrorNotice error={estimate.error} prefix="Could not work out what it would use" />
      {result ? (
        result.mutation_id ? (
          <p className="small" role="status">
            Drafted: {result.understood || 'a change to your story bible'}.{' '}
            <Link to={`/projects/${scope.projectId}/review/${encodeURIComponent(result.mutation_id)}`}>
              Review the change
            </Link>
          </p>
        ) : (
          <div className="small" role="status">
            <p>The assistant needs to know more before it can draft this:</p>
            <ul>
              {result.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </div>
        )
      ) : draft.error && isApiError(draft.error, 'not_confirmed') ? (
        <p className="hint" role="status">
          Not drafted, as you chose. Nothing was spent.
        </p>
      ) : (
        <ErrorNotice error={draft.error} prefix="Not drafted" />
      )}
      <div className="actions">
        {!result && (
          <Button
            size="sm"
            variant="primary"
            icon="spark"
            aria-describedby={`${id}-cost`}
            disabled={draft.isPending || (choice === 'make_true' && !passage)}
            onClick={() => draft.mutate()}
          >
            {draft.isPending ? 'Drafting…' : 'Draft the change'}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          {result ? 'Close' : 'Cancel'}
        </Button>
      </div>
    </div>
  )
}

// --- how the scene sounds (CON-018) --------------------------------------------------

const PERSON: Record<string, string> = {
  first: 'with “I”',
  second: 'with “you”',
  third: 'with “he” or “she”',
  unclear: 'not clear',
}
const TENSE: Record<string, string> = {
  past: 'past',
  present: 'present',
  mixed: 'past and present',
  unclear: 'not clear',
}

function percent(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function round(value: number | null | undefined): string {
  return value == null ? '—' : String(Math.round(value))
}

/**
 * How the scene's saved words sound against the author's usual: a few measures worked
 * out from the words alone, notes where the scene departs a long way from the other
 * scenes, and a choice to leave a scene out of the usual (a letter, a dream).
 */
export function Voice({
  planId,
  versionId,
  text,
  point,
  onShow,
  stale,
  canWrite,
}: {
  planId: string
  versionId: string | null
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  canWrite: boolean
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'scene-voice', planId, versionId ?? '')
  const voice = useQuery({ queryKey: key, queryFn: () => api.sceneVoice(scope, planId), retry: false })
  const count = useMutation({
    mutationFn: (leftOut: boolean) => api.setVoiceLeftOut(scope, planId, leftOut),
    onSuccess: (found) => client.setQueryData(key, found),
  })
  // Only a reading of this scene's voice counts (it always carries the usual).
  const found: SceneVoice | undefined = voice.data?.usual ? voice.data : undefined
  return (
    <section className="stack tight voice-panel" aria-labelledby="voice-heading">
      <div>
        <h2 id="voice-heading" className="studio-side-title">
          How it sounds
        </h2>
        <p className="hint">
          {found && found.usual.enough
            ? `Against your usual, from ${plural(found.usual.scenes, 'other scene')}${found.usual.own_only ? ' you wrote yourself' : ''}. No assistant needed.`
            : 'Against your usual voice in your other scenes. No assistant needed.'}
        </p>
      </div>
      {voice.isPending && <Loading>Measuring…</Loading>}
      {/* Extra to the scene: a failure is said quietly and never stops the writing. */}
      {voice.isError && <p className="muted small">How it sounds could not be worked out just now.</p>}
      {found && (
        <>
          {stale && found.stats && <p className="hint">This is about the saved text.</p>}
          {found.not_compared && <p className="muted small">{found.not_compared}</p>}
          {found.notes.length > 0 && (
            <ul className="findings">
              {found.notes.map((note, index) => (
                <li className="finding note" key={`${note.code}-${index}`}>
                  <Badge>Voice</Badge>
                  <p>{note.message}</p>
                  <Passage text={text} span={point(note.start, note.end)} onShow={onShow} />
                </li>
              ))}
            </ul>
          )}
          {!found.not_compared && found.notes.length === 0 && (
            <p className="muted small">It sounds like your other scenes.</p>
          )}
          {found.stats && <Measures voice={found} />}
          {found.stats && found.stats.echoes.length > 0 && (
            <div className="stack tight">
              <h3 className="voice-subtitle">Repeated close together</h3>
              <ul className="voice-echoes small">
                {found.stats.echoes.map((echo) => {
                  const span = point(echo.start, echo.end)
                  return (
                    <li key={`${echo.word}-${echo.start}`}>
                      “{echo.word}” {echo.times} times within 50 words
                      {span && (
                        <>
                          {' '}
                          <button type="button" className="link-button" onClick={() => onShow(span)}>
                            Show in the text
                          </button>
                        </>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
          {canWrite && found.stats && (
            <label className="check small">
              <input
                type="checkbox"
                checked={!found.left_out}
                disabled={count.isPending}
                onChange={(event) => count.mutate(!event.target.checked)}
              />
              <span>
                Count this scene in your usual
                <span className="hint block">
                  Leave out a scene meant to sound different, such as a letter or a dream.
                  {!found.left_out && !found.counted && ' Kept drafts count only while you have too few scenes of your own.'}
                </span>
              </span>
            </label>
          )}
          <ErrorNotice error={count.error} prefix="Not changed" />
        </>
      )}
    </section>
  )
}

function Measures({ voice }: { voice: SceneVoice }) {
  const stats = voice.stats
  if (!stats) return null
  const usual = voice.usual
  const most = Math.max(1, ...stats.bands)
  const rows: [string, string, string][] = [
    ['Sentence length', `about ${round(stats.sentence_mean)} words`, usual.enough ? `about ${round(usual.sentence_mean)}` : '—'],
    ['Dialogue', percent(stats.dialogue_share), usual.enough ? percent(usual.dialogue_share) : '—'],
  ]
  if (stats.english) {
    rows.push(
      ['Words ending in ‑ly', `${round(stats.adverb_rate)} in 1,000`, usual.enough ? `${round(usual.adverb_rate)} in 1,000` : '—'],
      ['Told', PERSON[stats.person] ?? stats.person, usual.enough ? (PERSON[usual.person] ?? usual.person) : '—'],
      ['Tense', TENSE[stats.tense] ?? stats.tense, usual.enough ? (TENSE[usual.tense] ?? usual.tense) : '—'],
    )
  }
  return (
    <div className="stack tight">
      <table className="voice-measures small">
        <caption className="sr-only">This scene and your usual</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Measure</span>
            </th>
            <th scope="col">This scene</th>
            <th scope="col">Your usual</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, scene, mine]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{scene}</td>
              <td>{mine}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="more">
        <summary>Sentence lengths in this scene</summary>
        <ul className="voice-bands small">
          {stats.bands.map((value, index) => (
            <li key={voice.band_labels[index]}>
              <span className="voice-band-label">{voice.band_labels[index]}</span>
              <span className="voice-band-bar" aria-hidden="true">
                <span style={{ width: `${(value / most) * 100}%` }} />
              </span>
              <span className="voice-band-count">{plural(value, 'sentence')}</span>
            </li>
          ))}
        </ul>
      </details>
      {!stats.english && <p className="hint">Some measures are worked out only for writing in English.</p>}
    </div>
  )
}
