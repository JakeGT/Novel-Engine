import type { CanonEntity } from '../api/client'
import { api } from '../api/client'
import { IMPORTANCE, IMPORTANCE_KEY, importanceOf } from '../lib/marks'
import { useCanonWrite, useProject } from '../project'
import { WriteProblem } from './ui'

/**
 * How much an entry matters to the story, changed where it is read (CTX-007): lists show
 * the entries that matter most first, and a scene's context gives them more room.
 * Saved like any other change to the entry, so it is in the history and can be undone.
 */
export function EntryImportance({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const current = importanceOf(entity)
  const write = useCanonWrite<CanonEntity, string>((expectedRevision, level) => {
    const attributes: Record<string, unknown> = { ...(entity.attributes ?? {}) }
    if (level) attributes[IMPORTANCE_KEY] = level
    else delete attributes[IMPORTANCE_KEY]
    return api.updateEntity(scope, entity.id, { attributes, expected_revision: expectedRevision })
  })
  const hint = IMPORTANCE.find((item) => item.value === current)?.hint
  return (
    <div className="entry-importance">
      <label className="field inline">
        <span>How much it matters</span>
        <select
          value={current}
          disabled={write.pending}
          onChange={(event) => void write.submit(event.target.value)}
        >
          <option value="">Not decided</option>
          {IMPORTANCE.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <span className="hint small muted">
        {hint ? `${hint}. ` : ''}The assistant gives entries that matter more room in a scene.
      </span>
      <WriteProblem write={write} scope={scope} />
    </div>
  )
}
