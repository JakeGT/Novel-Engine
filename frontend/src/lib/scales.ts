import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'

/** The story's shared scales (STY-006) and their version, read once and shared. */
export function useScales() {
  const { scope } = useProject()
  return useQuery({ queryKey: scopedKey(scope, 'scales'), queryFn: () => api.scales(scope) })
}
