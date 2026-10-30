import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { scopedKey, useEntries, useProject } from '../project'
import { readingScenes } from './plan'
import { useWorkingWhatIf } from './whatif'

/** What an answer can be limited to: the plan's scenes in reading order and the characters. */
export function useNameChoices() {
  const { scope } = useProject()
  const { whatIf } = useWorkingWhatIf(scope.projectId)
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const entries = useEntries(scope)
  const planned = readingScenes(outline.data?.items ?? [])
  const scenes = planned.map((scene) => ({ id: scene.id, label: scene.title }))
  // Scene id → whose eyes it is seen through, to offer that character first.
  const viewpoints: Record<string, string> = Object.fromEntries(
    planned.map((scene) => [scene.id, scene.refs.find((ref) => ref.role === 'point_of_view')?.entity_id ?? '']),
  )
  const characters = entries.items
    .filter((entry) => entry.lifecycle === 'active' && entry.type === 'Character')
    .map((entry) => ({ id: entry.id, label: entry.name }))
  const inWhatIf = Boolean(scope.branchId)
  return {
    scenes,
    characters,
    order: scenes.map((scene) => scene.id),
    viewpoints,
    inWhatIf,
    whatIfName: inWhatIf ? (whatIf?.name ?? '') : null,
  }
}
