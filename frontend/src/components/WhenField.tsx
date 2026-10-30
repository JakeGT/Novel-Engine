import { useId, useState } from 'react'
import type { WorldTime } from '../api/client'
import { unitWords, whenText } from '../lib/storytime'

type Kind = WorldTime['kind']

const KINDS: { value: Kind; label: string }[] = [
  { value: 'unknown', label: 'Not said' },
  { value: 'at', label: 'At a point' },
  { value: 'between', label: 'Between two points' },
  { value: 'relative', label: 'Before or after an event' },
]

function asNumber(text: string): number | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  return Number.isFinite(value) ? value : null
}

/**
 * When something happens in story time: not said, at a point, between two points, or
 * so long before or after another event. The author's own words can sit beside it.
 */
export function WhenField({
  legend,
  value,
  onChange,
  unit,
  events,
  disabled = false,
}: {
  legend: string
  value: WorldTime | null
  onChange: (value: WorldTime | null) => void
  unit: string
  /** Events a time can be set relative to. */
  events: { id: string; name: string }[]
  disabled?: boolean
}) {
  const name = useId()
  const kind: Kind = value?.kind ?? 'unknown'
  const label = value?.label ?? ''
  // Words alone ("some winter") are a time too; the box is offered, not imposed.
  const [describing, setDescribing] = useState(Boolean(label))
  const showWords = describing || kind !== 'unknown'
  const names = new Map(events.map((event) => [event.id, event.name]))

  const choose = (next: Kind) => {
    if (next === 'unknown') return onChange(label ? { kind: 'unknown', label } : null)
    if (next === 'at') return onChange({ kind: 'at', at: 0, label: label || null })
    if (next === 'between') return onChange({ kind: 'between', earliest: 0, latest: 1, label: label || null })
    onChange({
      kind: 'relative',
      anchor_event_id: events[0]?.id ?? '',
      offset: 1,
      direction: 'before',
      give_or_take: 0,
      label: label || null,
    })
  }
  const words = (text: string) => {
    if (!value) return onChange(text ? { kind: 'unknown', label: text } : null)
    onChange({ ...value, label: text || null } as WorldTime)
  }

  return (
    <fieldset className="when-field" disabled={disabled}>
      <legend>{legend}</legend>
      <div className="when-kinds" role="radiogroup" aria-label={`${legend}: kind of time`}>
        {KINDS.map((item) => (
          <label key={item.value} className="check">
            <input
              type="radio"
              name={name}
              checked={kind === item.value}
              disabled={item.value === 'relative' && events.length === 0}
              onChange={() => choose(item.value)}
            />
            <span>{item.label}</span>
          </label>
        ))}
      </div>
      {value?.kind === 'at' && (
        <div className="rule-line">
          <span>{unit}</span>
          <input
            aria-label={`${legend}: ${unit}`}
            inputMode="decimal"
            className="when-number"
            defaultValue={String(value.at)}
            onChange={(event) => {
              const at = asNumber(event.target.value)
              if (at !== null) onChange({ ...value, at })
            }}
          />
        </div>
      )}
      {value?.kind === 'between' && (
        <div className="rule-line">
          <span>between {unit}</span>
          <input
            aria-label={`${legend}: earliest ${unit}`}
            inputMode="decimal"
            className="when-number"
            defaultValue={value.earliest == null ? '' : String(value.earliest)}
            placeholder="any"
            onChange={(event) => onChange({ ...value, earliest: asNumber(event.target.value) })}
          />
          <span>and</span>
          <input
            aria-label={`${legend}: latest ${unit}`}
            inputMode="decimal"
            className="when-number"
            defaultValue={value.latest == null ? '' : String(value.latest)}
            placeholder="any"
            onChange={(event) => onChange({ ...value, latest: asNumber(event.target.value) })}
          />
        </div>
      )}
      {value?.kind === 'relative' && (
        <div className="rule-line">
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(value.give_or_take)}
              onChange={(event) =>
                onChange({
                  ...value,
                  give_or_take: event.target.checked ? Math.max(1, (value.offset ?? 0) / 3) : 0,
                })
              }
            />
            <span>about</span>
          </label>
          <input
            aria-label={`${legend}: how many ${unit}s`}
            inputMode="decimal"
            className="when-number"
            defaultValue={String(value.offset ?? 0)}
            onChange={(event) => {
              const offset = asNumber(event.target.value)
              if (offset !== null && offset >= 0) onChange({ ...value, offset })
            }}
          />
          <span>{unitWords(unit, value.offset ?? 0)}</span>
          <select
            aria-label={`${legend}: before or after`}
            value={value.direction ?? 'after'}
            onChange={(event) =>
              onChange({ ...value, direction: event.target.value as 'before' | 'after' })
            }
          >
            <option value="before">before</option>
            <option value="after">after</option>
          </select>
          <select
            aria-label={`${legend}: which event`}
            value={value.anchor_event_id}
            onChange={(event) => onChange({ ...value, anchor_event_id: event.target.value })}
          >
            {events.map((event) => (
              <option key={event.id} value={event.id}>
                {event.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {showWords ? (
        <label className="when-words">
          <span className="small muted">In your own words (optional)</span>
          <input value={label} maxLength={120} onChange={(event) => words(event.target.value)} />
          <span className="hint small muted">For example: the spring the river froze.</span>
        </label>
      ) : (
        <button type="button" className="link-button small" onClick={() => setDescribing(true)}>
          Describe it in words instead
        </button>
      )}
      {value && (
        <p className="reads-as" aria-live="polite">
          {value.label
            ? `Shown as “${value.label}”. Placed: ${whenText({ ...value, label: null } as WorldTime, unit, (id) => names.get(id))}.`
            : `Reads as: ${whenText(value, unit, (id) => names.get(id))}.`}
        </p>
      )}
    </fieldset>
  )
}
