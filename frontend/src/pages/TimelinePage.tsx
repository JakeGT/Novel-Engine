import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type CanonEvent, type Moment, type PlacedEvent, type WorldTime } from '../api/client'
import { Button, Dialog, EmptyState, ErrorNotice, Field, Loading, WriteProblem } from '../components/ui'
import { WhenField } from '../components/WhenField'
import { ContinuityChecks } from '../components/ContinuityChecks'
import { HowLongTable } from '../components/HowLongTable'
import { COMMON_UNITS, spanText, useStoryClock } from '../lib/storytime'
import { objectText, predicateLabel } from '../lib/words'
import { entryPath, scopedKey, useCanonWrite, useEntries, useInvalidateProject, useProject } from '../project'

/** Kinds of event the continuity checks read; the first is the default. */
const EVENT_KINDS = [
  { value: 'happening', label: 'Something happens' },
  { value: 'birth', label: 'Someone is born (with that one person in it)' },
  { value: 'death', label: 'Someone dies (with that one person in it)' },
  { value: 'founded', label: 'A place is founded or built (with the place in it)' },
  { value: 'destroyed', label: 'A place is destroyed or abandoned (with the place in it)' },
]

/** Place a new event, or change when an existing one happened. */
function EventDialog({ event, onClose }: { event?: CanonEvent; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const clock = useStoryClock(scope)
  const [name, setName] = useState(event?.name ?? '')
  // What the author wrote before the event was placed is offered as its words.
  const [when, setWhen] = useState<WorldTime | null>(
    event?.when ?? (event?.world_position ? { kind: 'unknown', label: event.world_position } : null),
  )
  const [who, setWho] = useState<string[]>([])
  const [kind, setKind] = useState('happening')
  const [problem, setProblem] = useState<string | null>(null)
  const people = entries.items.filter((item) => item.lifecycle === 'active')
  const others = clock.events.filter((item) => item.id !== event?.id)

  const save = useCanonWrite<CanonEvent>(
    (expectedRevision) =>
      event
        ? api.amendEvent(scope, event.id, {
            expected_revision: expectedRevision,
            name: name.trim() !== event.name ? name.trim() : undefined,
            when: when ?? { kind: 'unknown' },
          })
        : api.createEvent(scope, {
            kind,
            name: name.trim(),
            expected_revision: expectedRevision,
            participants: who.map((entity_id) => ({ entity_id, role: 'participant' })),
            location_ids: [],
            cause_ids: [],
            outcome_ids: [],
            when,
          }),
    () => onClose(),
  )
  const onSubmit = (submitted: FormEvent) => {
    submitted.preventDefault()
    if (!name.trim()) return setProblem('Say what happened.')
    setProblem(null)
    void save.submit()
  }

  return (
    <Dialog title={event ? `When did “${event.name}” happen?` : 'Add an event'} onClose={onClose} wide>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <Field label="What happened" error={problem}>
          {(props) => (
            <input
              {...props}
              data-autofocus
              value={name}
              maxLength={200}
              placeholder="The harbor fire"
              onChange={(change) => setName(change.target.value)}
            />
          )}
        </Field>
        {!event && (
          <Field
            label="What kind of event"
            hint="A birth, a death, or a place built or destroyed is used to check who and where can be in a scene then."
          >
            {(props) => (
              <select {...props} value={kind} onChange={(change) => setKind(change.target.value)}>
                {EVENT_KINDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        {!event && people.length > 0 && (
          <fieldset className="stack tight">
            <legend>Who or what was part of it (optional)</legend>
            <div className="when-kinds">
              {people.slice(0, 40).map((item) => (
                <label key={item.id} className="check">
                  <input
                    type="checkbox"
                    checked={who.includes(item.id)}
                    onChange={() =>
                      setWho(who.includes(item.id) ? who.filter((id) => id !== item.id) : [...who, item.id])
                    }
                  />
                  <span>{item.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <WhenField legend="When" value={when} onChange={setWhen} unit={clock.unit} events={others} />
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={save.pending}>
            {save.pending ? 'Saving…' : event ? 'Save when it happened' : 'Add the event'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function UnitForm({ unit }: { unit: string }) {
  const { scope, canWrite } = useProject()
  const invalidate = useInvalidateProject(scope)
  const [value, setValue] = useState(unit)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const result = await api.setTimeUnit(scope, value)
      setValue(result.unit)
      setSaved(true)
      await invalidate()
    } catch (failure) {
      setError(failure)
    } finally {
      setSaving(false)
    }
  }
  return (
    <form className="rule-line" onSubmit={(event) => void submit(event)} aria-label="Unit of story time">
      <label htmlFor="time-unit">Story time is counted in</label>
      <input
        id="time-unit"
        list="time-units"
        className="when-number"
        value={value}
        disabled={!canWrite}
        onChange={(event) => {
          setValue(event.target.value)
          setSaved(false)
        }}
      />
      <datalist id="time-units">
        {COMMON_UNITS.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      {canWrite && (
        value.trim() !== unit && (
          <Button size="sm" type="submit" disabled={saving}>
            Save
          </Button>
        )
      )}
      {saved && <span className="muted small" role="status">Saved.</span>}
      <ErrorNotice error={error} prefix="Not saved" />
    </form>
  )
}

export function TimelinePage() {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const clock = useStoryClock(scope)
  const timeline = clock.timeline
  const [editing, setEditing] = useState<{ event?: CanonEvent } | null>(null)
  const [at, setAt] = useState('')
  const [moment, setMoment] = useState<number | null>(null)
  const location = useLocation()
  const loaded = Boolean(timeline.data)
  // A link from a continuity finding opens the timeline at its event.
  useEffect(() => {
    if (loaded && location.hash.startsWith('#event-')) {
      document.getElementById(location.hash.slice(1))?.scrollIntoView?.({ block: 'center' })
    }
  }, [loaded, location.hash])
  const checks = useQuery({
    queryKey: scopedKey(scope, 'timeline-checks'),
    queryFn: () => api.continuity(scope),
  })
  const momentQuery = useQuery({
    queryKey: scopedKey(scope, 'timeline-moment', moment),
    queryFn: () => api.moment(scope, moment as number),
    enabled: moment !== null,
  })

  const who = (event: CanonEvent) =>
    event.participants
      .map((item) => entries.names.get(item.entity_id))
      .filter(Boolean)
      .join(', ')

  const row = (item: PlacedEvent) => (
    <li key={item.event.id} id={`event-${item.event.id}`}>
      <span className="when">
        {item.when}
        {resolvedText(item, clock.unit) && <span className="resolved">{resolvedText(item, clock.unit)}</span>}
      </span>
      <span>
        {item.event.name}
        {who(item.event) && <span className="who muted">With {who(item.event)}</span>}
      </span>
      {canWrite && (
        <Button size="sm" variant="ghost" onClick={() => setEditing({ event: item.event })}>
          Change when
        </Button>
      )}
    </li>
  )

  const data = timeline.data
  const empty = data && data.placed.length + data.unplaced.length + data.spans.length === 0

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Timeline</h1>
          <p className="lede">
            When things happen in your story. Nothing here is required: an event can stay “not
            placed in time” for as long as you like.
          </p>
        </div>
        {canWrite && (
          <Button variant="primary" icon="plus" onClick={() => setEditing({})}>
            Add an event
          </Button>
        )}
      </div>

      <div className="section-gap">
      {data && <UnitForm key={data.unit} unit={data.unit} />}
      <ErrorNotice error={timeline.error} prefix="Could not load the timeline" />
      {timeline.isPending && <Loading>Loading the timeline…</Loading>}

      {data?.issues.map((issue) => (
        <p key={issue.code + issue.record_ids.join()} className="notice warn" role="alert">
          {issue.message}
        </p>
      ))}

      <ErrorNotice error={checks.error} prefix="Could not check continuity" />
      {/* Scenes placed in time are checked too, so findings can show on an empty timeline. */}
      {data && checks.data && (!empty || checks.data.findings.length > 0) && <ContinuityChecks data={checks.data} />}

      {empty && (
        <EmptyState icon="timeline" title="Nothing on the timeline yet">
          Add an event and say when it happened, or tell the assistant: “The war began in year
          100, and Mara left three years before it.”
        </EmptyState>
      )}

      {data && data.placed.length > 0 && (
        <section className="stack tight" aria-labelledby="in-order">
          <h2 id="in-order">In order</h2>
          <ol className="timeline-list">{data.placed.map(row)}</ol>
        </section>
      )}

      {data && data.unplaced.length > 0 && (
        <section className="stack tight" aria-labelledby="unplaced">
          <h2 id="unplaced">Not placed in time</h2>
          <ul className="rows">
            {data.unplaced.map((item) => (
              <li className="row" key={item.event.id} id={`event-${item.event.id}`}>
                <span className="grow">{item.event.name}</span>
                {item.event.world_position && (
                  <span className="muted small">You wrote: “{item.event.world_position}”</span>
                )}
                {canWrite && (
                  <Button size="sm" onClick={() => setEditing({ event: item.event })}>
                    Place in time
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.spans.length > 0 && (
        <section className="stack tight" aria-labelledby="spans">
          <h2 id="spans">True for part of the story</h2>
          <ul className="rows">
            {data.spans.map((item) => (
              <li className="row" key={item.fact.id}>
                <span className="grow sentence">
                  <Link to={entryPath(scope, item.fact.subject_id)}>
                    {entries.names.get(item.fact.subject_id) ?? 'An entry'}
                  </Link>{' '}
                  {predicateLabel(item.fact.predicate)}{' '}
                  {objectText(item.fact.object as Record<string, unknown>, entries.names)}
                </span>
                <span className="muted small">
                  {spanText(item.fact.starts, item.fact.ends, clock.unit, clock.eventName)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && (
        <section className="stack tight" aria-labelledby="how-long-heading">
          <h2 id="how-long-heading">How long things take</h2>
          <p className="lede small">
            Your own travel times and lengths, used by the continuity checks. Nothing is guessed: a journey you have
            not set is never checked.
          </p>
          <HowLongTable unit={data.unit} events={clock.events} spans={data.spans} />
        </section>
      )}

      {data && (data.placed.length > 0 || data.spans.length > 0) && (
        <section className="stack tight" aria-labelledby="moment">
          <h2 id="moment">What was true at a moment</h2>
          <form
            className="rule-line"
            onSubmit={(event) => {
              event.preventDefault()
              const value = Number(at)
              if (at.trim() && Number.isFinite(value)) setMoment(value)
            }}
          >
            <label htmlFor="moment-at">At {clock.unit}</label>
            <input
              id="moment-at"
              className="when-number"
              inputMode="decimal"
              value={at}
              onChange={(event) => setAt(event.target.value)}
            />
            <Button size="sm" type="submit" disabled={!at.trim()}>
              Show
            </Button>
          </form>
          <ErrorNotice error={momentQuery.error} />
          {momentQuery.data && <MomentView moment={momentQuery.data} />}
        </section>
      )}

      </div>
      {editing && <EventDialog event={editing.event} onClose={() => setEditing(null)} />}
    </>
  )
}

/** Where a time worded by the author actually falls, when that adds something. */
function resolvedText(item: PlacedEvent, unit: string): string {
  const { earliest, latest } = item
  if (earliest == null && latest == null) return ''
  const plain = (value: number) => String(Number(value.toFixed(2)))
  const text =
    earliest != null && latest != null
      ? earliest === latest
        ? `${unit} ${plain(earliest)}`
        : `${unit}s ${plain(earliest)}–${plain(latest)}`
      : earliest != null
        ? `after ${unit} ${plain(earliest)}`
        : `before ${unit} ${plain(latest as number)}`
  return text === item.when ? '' : text
}

function MomentView({ moment }: { moment: Moment }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  return (
    <div className="stack tight">
      <h3 className="label">Had happened by {moment.unit} {moment.at}</h3>
      {moment.happened.length === 0 ? (
        <p className="muted">Nothing placed in time had happened yet.</p>
      ) : (
        <ul className="rows">
          {moment.happened.map((item) => (
            <li className="row" key={item.event.id}>
              <span className="grow">{item.event.name}</span>
              <span className="muted small">{item.when}</span>
            </li>
          ))}
        </ul>
      )}
      <h3 className="label" style={{ marginTop: '0.9rem' }}>
        True at {moment.unit} {moment.at}
      </h3>
      <ul className="rows">
        {moment.facts.map((item) => (
          <li className="row" key={item.fact.id}>
            <span className="grow sentence">
              {entries.names.get(item.fact.subject_id) ?? 'An entry'} {predicateLabel(item.fact.predicate)}{' '}
              {objectText(item.fact.object as Record<string, unknown>, entries.names)}
            </span>
            {item.truth === 'maybe' && <span className="knowledge-tag">Maybe: its start or end is uncertain</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}
