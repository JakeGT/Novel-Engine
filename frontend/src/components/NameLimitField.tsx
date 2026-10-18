import { useId } from 'react'
import type { NameLimit, Only } from '../lib/names'

interface Choice {
  id: string
  label: string
}

/**
 * Where an answer about a name holds. Everywhere is the usual answer; the others keep
 * it to one text, a stretch of scenes, or scenes seen by one character. Inside a what-if
 * the answer stays in it unless the author says otherwise.
 */
export function NameLimitField({
  value,
  onChange,
  scenes,
  characters,
  textTitle,
  currentSceneId,
  whatIfName,
  problem,
  disabled = false,
}: {
  value: NameLimit
  onChange: (value: NameLimit) => void
  /** The plan's scenes in reading order. */
  scenes: Choice[]
  characters: Choice[]
  /** Set when answering in a text: offers "only in this text". */
  textTitle?: string
  /** Set when the text is a scene's: the scene choices read "this scene". */
  currentSceneId?: string | null
  /** Set (possibly empty) when working in a what-if. */
  whatIfName?: string | null
  problem?: string | null
  disabled?: boolean
}) {
  const id = useId()
  const set = (change: Partial<NameLimit>) => onChange({ ...value, ...change })
  const here = currentSceneId ? scenes.find((scene) => scene.id === currentSceneId) : undefined
  const options: { value: Only; label: string; shown: boolean }[] = [
    { value: 'everywhere', label: 'Everywhere', shown: true },
    { value: 'text', label: 'Only in this text', shown: Boolean(textTitle) },
    { value: 'from', label: here ? 'Only from this scene on' : 'Only from a scene on', shown: scenes.length > 0 },
    { value: 'until', label: here ? 'Only until this scene' : 'Only until a scene', shown: scenes.length > 0 },
    { value: 'between', label: 'Only between two scenes', shown: scenes.length > 1 },
    { value: 'seen_by', label: 'Only when seen by a character', shown: characters.length > 0 },
  ]
  const sceneSelect = (label: string, key: 'fromScene' | 'untilScene') => (
    <span className="name-limit-part">
      <label htmlFor={`${id}-${key}`}>{label}</label>
      <select
        id={`${id}-${key}`}
        value={value[key]}
        disabled={disabled}
        onChange={(event) => set({ [key]: event.target.value })}
      >
        <option value="">Choose a scene…</option>
        {scenes.map((scene, index) => (
          <option key={scene.id} value={scene.id}>
            {index + 1}. {scene.label}
            {scene.id === currentSceneId ? ' (this scene)' : ''}
          </option>
        ))}
      </select>
    </span>
  )

  return (
    <div className="field name-limit">
      <label htmlFor={`${id}-only`}>Where it holds</label>
      <div className="name-limit-row">
        <select
          id={`${id}-only`}
          value={value.only}
          disabled={disabled}
          aria-describedby={`${id}-hint${problem ? ` ${id}-error` : ''}`}
          onChange={(event) => set({ only: event.target.value as Only })}
        >
          {options
            .filter((option) => option.shown)
            .map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
        </select>
        {(value.only === 'from' || value.only === 'between') && sceneSelect('From', 'fromScene')}
        {(value.only === 'until' || value.only === 'between') && sceneSelect(value.only === 'between' ? 'To' : 'Until', 'untilScene')}
        {value.only === 'seen_by' && (
          <span className="name-limit-part">
            <label htmlFor={`${id}-viewpoint`}>Seen by</label>
            <select
              id={`${id}-viewpoint`}
              value={value.viewpoint}
              disabled={disabled}
              onChange={(event) => set({ viewpoint: event.target.value })}
            >
              <option value="">Choose a character…</option>
              {characters.map((character) => (
                <option key={character.id} value={character.id}>
                  {character.label}
                </option>
              ))}
            </select>
          </span>
        )}
      </div>
      <span id={`${id}-hint`} className="hint">
        Where several answers hold, the narrowest one decides.
      </span>
      {whatIfName !== null && whatIfName !== undefined && (
        <label className="check small">
          <input
            type="checkbox"
            checked={value.whatIfOnly}
            disabled={disabled}
            onChange={(event) => set({ whatIfOnly: event.target.checked })}
          />
          {whatIfName ? `Only in the what-if “${whatIfName}”` : 'Only in this what-if'}
        </label>
      )}
      {problem && (
        <span id={`${id}-error`} className="field-error" role="alert">
          {problem}
        </span>
      )}
    </div>
  )
}
