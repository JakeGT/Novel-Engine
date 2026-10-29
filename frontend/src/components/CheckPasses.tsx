import { useId } from 'react'
import type { CheckPass, PassEstimate } from '../api/client'
import { ALL_PASSES, OPTIONAL_PASSES, PASS_NAMES, PASS_ORDER, tokenCount } from '../lib/studio'

// Choosing what the assistant checks (CON-008): each pass is one request of its own,
// about one thing, with its cost said beside it before anything is sent. The optional
// passes are listed apart, off until ticked.

function PassRow({
  item,
  found,
  chosen,
  disabled,
  toggle,
}: {
  item: CheckPass
  found: PassEstimate | undefined
  chosen: boolean
  disabled: boolean
  toggle: (item: CheckPass, on: boolean) => void
}) {
  return (
    <li>
      <label className="check">
        <input type="checkbox" checked={chosen} disabled={disabled} onChange={(event) => toggle(item, event.target.checked)} />
        <span className="pass-text">
          <span className="pass-line">
            <span className="pass-name">{found?.label ?? PASS_NAMES[item]}</span>
            <span className="pass-cost">
              {found ? `about ${tokenCount(found.total_tokens)} tokens` : 'working out the cost…'}
            </span>
          </span>
          {found?.description && <span className="pass-about">{found.description}</span>}
        </span>
      </label>
    </li>
  )
}

export function PassChoices({
  passes,
  chosen,
  onChange,
  legend = 'What the assistant checks',
  hint = 'Each is a request of its own, about that one thing.',
  disabled = false,
}: {
  /** The estimate for each pass; absent while it loads. */
  passes: PassEstimate[] | undefined
  chosen: CheckPass[]
  onChange: (next: CheckPass[]) => void
  legend?: string
  hint?: string
  disabled?: boolean
}) {
  const hintId = useId()
  const moreId = useId()
  const byPass = new Map(passes?.map((item) => [item.check_pass, item]))
  const toggle = (item: CheckPass, on: boolean) =>
    onChange(ALL_PASSES.filter((other) => (other === item ? on : chosen.includes(other))))
  // The pictures pass only where the scene has pictures it can use.
  const offered: CheckPass[] = (byPass.get('pictures')?.total_tokens ?? 0) > 0 ? [...PASS_ORDER, 'pictures'] : PASS_ORDER
  const row = (item: CheckPass) => (
    <PassRow
      key={item}
      item={item}
      found={byPass.get(item)}
      chosen={chosen.includes(item)}
      disabled={disabled}
      toggle={toggle}
    />
  )
  return (
    <fieldset className="pass-choices" aria-describedby={hintId}>
      <legend>{legend}</legend>
      <p id={hintId} className="hint">
        {hint}
      </p>
      <ul className="pass-list">{offered.map(row)}</ul>
      <p id={moreId} className="hint pass-more">
        More you can add. Each is one more request, and is off until you tick it.
      </p>
      <ul className="pass-list" aria-labelledby={moreId}>
        {OPTIONAL_PASSES.map(row)}
      </ul>
    </fieldset>
  )
}
