import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type CanonEntity, type CanonEvent, type HowLong, type SpannedFact } from '../api/client'
import { unitWords } from '../lib/storytime'
import { objectText, predicateLabel } from '../lib/words'
import { scopedKey, useEntries, useInvalidateProject, useProject } from '../project'
import { Button, ErrorNotice, Loading } from './ui'

interface Journey {
  from_id: string
  to_id: string
  amount: string
}

interface Length {
  // "event:<id>" or "fact:<id>"
  subject: string
  amount: string
  give_or_take: string
}

function journeys(table: HowLong): Journey[] {
  return table.travel.map((row) => ({ from_id: row.from_id, to_id: row.to_id, amount: String(row.amount) }))
}

function lengths(table: HowLong): Length[] {
  return table.lasting.map((row) => ({
    subject: `${row.subject_kind}:${row.subject_id}`,
    amount: String(row.amount),
    give_or_take: row.give_or_take ? String(row.give_or_take) : '',
  }))
}

interface Props {
  unit: string
  events: CanonEvent[]
  spans: SpannedFact[]
}

/**
 * The story's own table of how long things take: journeys between places and how long
 * an event or a fact lasts. The continuity checks read it; nothing ever fills it in by
 * guessing, and places with no journey here are never checked for travel.
 */
export function HowLongTable({ unit, events, spans }: Props) {
  const { scope } = useProject()
  const query = useQuery({ queryKey: scopedKey(scope, 'how-long'), queryFn: () => api.howLong(scope) })
  // Kept here: a save brings a new version, which starts the form afresh.
  const [saved, setSaved] = useState(false)
  if (query.isPending) return <Loading>Loading how long things take…</Loading>
  if (query.error || !query.data) return <ErrorNotice error={query.error} prefix="Could not load how long things take" />
  return (
    <HowLongForm
      key={query.data.version}
      table={query.data}
      unit={unit}
      events={events}
      spans={spans}
      saved={saved}
      setSaved={setSaved}
    />
  )
}

interface FormProps extends Props {
  table: HowLong
  saved: boolean
  setSaved: (saved: boolean) => void
}

function HowLongForm({ table, unit, events, spans, saved, setSaved }: FormProps) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  // Every place, however many entries the story has.
  const placesQuery = useQuery({
    queryKey: scopedKey(scope, 'entities', 'places'),
    queryFn: () => api.listEntities(scope, { entity_type: 'Location', limit: 200 }),
  })
  const invalidate = useInvalidateProject(scope)
  const [travel, setTravel] = useState<Journey[]>(() => journeys(table))
  const [lasting, setLasting] = useState<Length[]>(() => lengths(table))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const places = (placesQuery.data?.items ?? []).filter(
    (item: CanonEntity) => item.type === 'Location' && item.lifecycle === 'active',
  )
  const many = unitWords(unit, 2)
  const placeName = (id: string) =>
    places.find((place) => place.id === id)?.name ?? entries.names.get(id) ?? 'A place no longer in the story bible'
  const eventName = new Map(events.map((event) => [event.id, event.name]))
  const factWords = (item: SpannedFact) =>
    `${entries.names.get(item.fact.subject_id) ?? 'An entry'} ${predicateLabel(item.fact.predicate)} ${objectText(
      item.fact.object as Record<string, unknown>,
      entries.names,
    )}`
  const subjectName = (subject: string) => {
    const [kind, id] = subject.split(/:(.*)/s)
    if (kind === 'event') return eventName.get(id) ?? 'An event no longer in the story'
    const fact = spans.find((item) => item.fact.id === id)
    return fact ? factWords(fact) : 'A fact no longer in the story bible'
  }
  const dirty =
    JSON.stringify(travel) !== JSON.stringify(journeys(table)) ||
    JSON.stringify(lasting) !== JSON.stringify(lengths(table))

  const change = <T,>(set: (fn: (rows: T[]) => T[]) => void, index: number, patch: Partial<T>) => {
    setSaved(false)
    set((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    // Checked here first so the words stay as typed and the problem sits beside them.
    const pairs = new Set<string>()
    for (const row of travel) {
      const amount = Number(row.amount)
      if (!row.from_id || !row.to_id) return setProblem('Choose both places for each journey.')
      if (row.from_id === row.to_id) return setProblem('A journey is between two different places.')
      if (row.amount.trim() === '' || !Number.isFinite(amount) || amount < 0)
        return setProblem(`Say how many ${many} each journey takes, or 0 when one place is inside the other.`)
      const pair = [row.from_id, row.to_id].sort().join('|')
      if (pairs.has(pair)) return setProblem('Each pair of places has one travel time.')
      pairs.add(pair)
    }
    const subjects = new Set<string>()
    for (const row of lasting) {
      const amount = Number(row.amount)
      if (!row.subject) return setProblem('Choose what each length is about.')
      if (row.amount.trim() === '' || !Number.isFinite(amount) || amount <= 0)
        return setProblem(`Say how many ${many} each thing lasts.`)
      if (subjects.has(row.subject)) return setProblem('Each event or fact has one length.')
      subjects.add(row.subject)
    }
    setProblem(null)
    setSaving(true)
    try {
      await api.setHowLong(scope, {
        travel: travel.map((row) => ({ from_id: row.from_id, to_id: row.to_id, amount: Number(row.amount) })),
        lasting: lasting.map((row) => {
          const [kind, id] = row.subject.split(/:(.*)/s)
          return {
            subject_kind: kind as 'event' | 'fact',
            subject_id: id,
            amount: Number(row.amount),
            give_or_take: Number(row.give_or_take || 0),
          }
        }),
        expected_version: table.version,
      })
      setSaved(true)
      await invalidate()
    } catch (failure) {
      setError(failure)
    } finally {
      setSaving(false)
    }
  }

  if (!canWrite) {
    if (table.travel.length + table.lasting.length === 0) return <p className="muted">No travel times or lengths yet.</p>
    return (
      <ul className="rows">
        {table.travel.map((row) => (
          <li className="row" key={`${row.from_id}-${row.to_id}`}>
            {placeName(row.from_id)} to {placeName(row.to_id)}: {row.amount} {unitWords(unit, row.amount)}
          </li>
        ))}
        {table.lasting.map((row) => (
          <li className="row" key={`${row.subject_kind}-${row.subject_id}`}>
            {subjectName(`${row.subject_kind}:${row.subject_id}`)} lasts {row.amount} {unitWords(unit, row.amount)}
          </li>
        ))}
      </ul>
    )
  }

  const lastable = [
    ...events.map((item) => ({ value: `event:${item.id}`, label: item.name, group: 'Events' })),
    ...spans.map((item) => ({ value: `fact:${item.fact.id}`, label: factWords(item), group: 'True for part of the story' })),
  ]

  return (
    <form className="stack how-long" onSubmit={(event) => void submit(event)} aria-label="How long things take" noValidate>
      <fieldset className="stack tight">
        <legend>Journeys between places</legend>
        <p className="muted small">
          How long it takes to get from one place to the other, either way. Put 0 when one place is inside the other.
          Places with no journey here are never checked for travel.
        </p>
        {placesQuery.isSuccess && places.length < 2 && <p className="muted small">Add at least two places to the story bible to set a journey.</p>}
        {travel.map((row, index) => (
          <div className="rule-line how-long-row" key={index} role="group" aria-label={`Journey ${index + 1}`}>
            <label className="sr-only" htmlFor={`journey-from-${index}`}>
              From
            </label>
            <select id={`journey-from-${index}`} value={row.from_id} onChange={(e) => change(setTravel, index, { from_id: e.target.value })}>
              <option value="">Choose a place</option>
              {places.map((place) => (
                <option key={place.id} value={place.id}>
                  {place.name}
                </option>
              ))}
            </select>
            <span aria-hidden="true">to</span>
            <label className="sr-only" htmlFor={`journey-to-${index}`}>
              To
            </label>
            <select id={`journey-to-${index}`} value={row.to_id} onChange={(e) => change(setTravel, index, { to_id: e.target.value })}>
              <option value="">Choose a place</option>
              {places.map((place) => (
                <option key={place.id} value={place.id}>
                  {place.name}
                </option>
              ))}
            </select>
            <label htmlFor={`journey-takes-${index}`}>takes</label>
            <input
              id={`journey-takes-${index}`}
              className="when-number"
              inputMode="decimal"
              value={row.amount}
              onChange={(e) => change(setTravel, index, { amount: e.target.value })}
            />
            <span>{many}</span>
            <Button size="sm" variant="ghost" onClick={() => setTravel((rows) => rows.filter((_, i) => i !== index))}>
              Remove this journey
            </Button>
          </div>
        ))}
        {places.length >= 2 && (
          <Button
            size="sm"
            icon="plus"
            className="start"
            onClick={() => setTravel((rows) => [...rows, { from_id: '', to_id: '', amount: '' }])}
          >
            Add a journey
          </Button>
        )}
      </fieldset>

      <fieldset className="stack tight">
        <legend>How long things last</legend>
        <p className="muted small">
          An event placed between two times, or a fact with a start and an end, is checked against how long you say it
          lasts.
        </p>
        {lastable.length === 0 && (
          <p className="muted small">Place an event in time, or give a fact a start and an end, to say how long it lasts.</p>
        )}
        {lasting.map((row, index) => (
          <div className="rule-line how-long-row" key={index} role="group" aria-label={`Length ${index + 1}`}>
            <label className="sr-only" htmlFor={`length-of-${index}`}>
              What lasts
            </label>
            <select id={`length-of-${index}`} value={row.subject} onChange={(e) => change(setLasting, index, { subject: e.target.value })}>
              <option value="">Choose an event or a fact</option>
              {row.subject && !lastable.some((item) => item.value === row.subject) && (
                <option value={row.subject}>{subjectName(row.subject)}</option>
              )}
              {['Events', 'True for part of the story'].map((group) => {
                const options = lastable.filter((item) => item.group === group)
                return options.length ? (
                  <optgroup key={group} label={group}>
                    {options.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                ) : null
              })}
            </select>
            <label htmlFor={`length-amount-${index}`}>lasts</label>
            <input
              id={`length-amount-${index}`}
              className="when-number"
              inputMode="decimal"
              value={row.amount}
              onChange={(e) => change(setLasting, index, { amount: e.target.value })}
            />
            <span>{many},</span>
            <label htmlFor={`length-about-${index}`}>give or take</label>
            <input
              id={`length-about-${index}`}
              className="when-number"
              inputMode="decimal"
              placeholder="0"
              value={row.give_or_take}
              onChange={(e) => change(setLasting, index, { give_or_take: e.target.value })}
            />
            <Button size="sm" variant="ghost" onClick={() => setLasting((rows) => rows.filter((_, i) => i !== index))}>
              Remove this length
            </Button>
          </div>
        ))}
        {lastable.length > 0 && (
          <Button
            size="sm"
            icon="plus"
            className="start"
            onClick={() => setLasting((rows) => [...rows, { subject: '', amount: '', give_or_take: '' }])}
          >
            Say how long something lasts
          </Button>
        )}
      </fieldset>

      {problem && (
        <p className="notice danger" role="alert">
          {problem}
        </p>
      )}
      <ErrorNotice error={error} prefix="Not saved" />
      <div className="actions">
        <Button variant="primary" type="submit" disabled={saving || !dirty}>
          {saving ? 'Saving…' : 'Save how long things take'}
        </Button>
        {saved && !dirty && (
          <span className="muted small" role="status">
            Saved. The checks above now use it.
          </span>
        )}
      </div>
    </form>
  )
}
