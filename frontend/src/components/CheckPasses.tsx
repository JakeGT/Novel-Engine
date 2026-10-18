import { useId } from 'react'
import type { CheckPass, PassEstimate } from '../api/client'
import { PASS_NAMES, PASS_ORDER, tokenCount } from '../lib/studio'

// Choosing what the assistant checks (CON-008): each pass is one request of its own,
// about one thing, with its cost said beside it before anything is sent.

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
  const byPass = new Map(passes?.map((item) => [item.check_pass, item]))
  const toggle = (item: CheckPass, on: boolean) =>
    onChange(PASS_ORDER.filter((other) => (other === item ? on : chosen.includes(other))))
  return (
    <fieldset className="pass-choices" aria-describedby={hintId}>
      <legend>{legend}</legend>
      <p id={hintId} className="hint">
        {hint}
      </p>
      <ul className="pass-list">
        {PASS_ORDER.map((item) => {
          const found = byPass.get(item)
          return (
            <li key={item}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={chosen.includes(item)}
                  disabled={disabled}
                  onChange={(event) => toggle(item, event.target.checked)}
                />
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
        })}
      </ul>
    </fieldset>
  )
}
