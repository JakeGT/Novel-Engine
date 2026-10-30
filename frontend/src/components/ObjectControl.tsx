import type { CanonEntity, VocabularyPredicate } from '../api/client'
import type { Entered } from '../lib/facts'
import { typeLabel } from '../lib/words'

/** The end of a fact's sentence: an entry to point to, or a value of the right kind. */
export function ObjectControl({
  predicate,
  entered,
  onChange,
  targets,
  label,
  disabled = false,
}: {
  predicate: VocabularyPredicate
  entered: Entered
  onChange: (entered: Entered) => void
  targets: Pick<CanonEntity, 'id' | 'name' | 'type'>[]
  label: string
  disabled?: boolean
}) {
  const change = (value: string) => onChange({ ...entered, value })
  if (predicate.object_shape === 'entity_ref') {
    return (
      <select aria-label={label} value={entered.value} disabled={disabled} onChange={(event) => change(event.target.value)}>
        <option value="">Choose…</option>
        {targets.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name} ({typeLabel(item.type)})
          </option>
        ))}
      </select>
    )
  }
  if (predicate.object_shape === 'boolean') {
    return (
      <select aria-label={label} value={entered.value} disabled={disabled} onChange={(event) => change(event.target.value)}>
        <option value="">Choose…</option>
        <option value="true">yes</option>
        <option value="false">no</option>
      </select>
    )
  }
  if (predicate.object_shape === 'quantity') {
    return (
      <>
        <label className="mini">
          <span>Amount</span>
          <input
            aria-label="Amount"
            disabled={disabled}
            inputMode="decimal"
            placeholder="12.5"
            value={entered.value}
            onChange={(event) => change(event.target.value)}
          />
        </label>
        <label className="mini">
          <span>Unit</span>
          <input
            aria-label="Unit"
            disabled={disabled}
            placeholder="km"
            value={entered.unit}
            onChange={(event) => onChange({ ...entered, unit: event.target.value })}
          />
        </label>
      </>
    )
  }
  return (
    <input
      aria-label={label}
      disabled={disabled}
      inputMode={['integer', 'decimal'].includes(predicate.object_shape) ? 'decimal' : undefined}
      value={entered.value}
      onChange={(event) => change(event.target.value)}
    />
  )
}
