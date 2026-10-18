import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'

/**
 * Choose the scene something happens in, if any: so what people learn there can be
 * placed in the story's order (KNW-002). Shows nothing while the plan has no scenes.
 */
export function useScenePick(initial = '') {
  const { scope } = useProject()
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scenes = (outline.data?.items ?? []).filter((item) => item.kind === 'scene')
  const [sceneId, setSceneId] = useState(initial)
  const picker =
    scenes.length > 0 ? (
      <label className="field">
        <span>In which scene (optional)</span>
        <select value={sceneId} onChange={(event) => setSceneId(event.target.value)}>
          <option value="">Not in a particular scene</option>
          {scenes.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
        <span className="hint small muted">
          Placing it lets the checks see a scene that uses it before it is learned.
        </span>
      </label>
    ) : null
  return { sceneId: sceneId || null, picker }
}
