// Story time in words (ADR-020). Mirrors `describe` in backend/domain/worldtime.py so
// a time reads the same wherever it is shown; the author's own words always win.
import { useQuery } from '@tanstack/react-query'
import { api, type Scope, type WorldTime } from '../api/client'
import { scopedKey } from '../project'

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)))
}

export function unitWords(unit: string, amount: number): string {
  return amount === 1 ? unit : `${unit}s`
}

export function whenText(
  when: WorldTime | null | undefined,
  unit: string,
  eventName: (id: string) => string | undefined = () => undefined,
): string {
  if (!when || when.kind === 'unknown') return when?.label || 'not placed in time'
  if (when.label) return when.label
  if (when.kind === 'at') return `${unit} ${number(when.at)}`
  if (when.kind === 'between') {
    const { earliest, latest } = when
    if (earliest != null && latest != null) return `between ${unit} ${number(earliest)} and ${number(latest)}`
    if (earliest != null) return `after ${unit} ${number(earliest)}`
    return `before ${unit} ${number(latest ?? 0)}`
  }
  const anchor = eventName(when.anchor_event_id) ?? 'an event no longer in the story'
  const offset = when.offset ?? 0
  if (offset === 0) return `at the time of ${anchor}`
  const about = when.give_or_take ? 'about ' : ''
  return `${about}${number(offset)} ${unitWords(unit, offset)} ${when.direction ?? 'after'} ${anchor}`
}

/** "from year 10 until year 20", or "" when nothing is said. */
export function spanText(
  starts: WorldTime | null | undefined,
  ends: WorldTime | null | undefined,
  unit: string,
  eventName?: (id: string) => string | undefined,
): string {
  const parts = []
  if (starts && starts.kind !== 'unknown') parts.push(`from ${whenText(starts, unit, eventName)}`)
  if (ends && ends.kind !== 'unknown') parts.push(`until ${whenText(ends, unit, eventName)}`)
  return parts.join(' ')
}

/** Units an author is likely to count story time in, offered as suggestions. */
export const COMMON_UNITS = ['year', 'month', 'day', 'season', 'hour', 'age']

/** The story's unit of time and its events, for any screen that shows or sets a time. */
export function useStoryClock(scope: Scope) {
  const timeline = useQuery({
    queryKey: scopedKey(scope, 'timeline'),
    queryFn: () => api.timeline(scope),
  })
  const events = [...(timeline.data?.placed ?? []), ...(timeline.data?.unplaced ?? [])].map(
    (item) => item.event,
  )
  const names = new Map(events.map((event) => [event.id, event.name]))
  return {
    unit: timeline.data?.unit ?? 'year',
    events,
    eventName: (id: string) => names.get(id),
    timeline,
  }
}
