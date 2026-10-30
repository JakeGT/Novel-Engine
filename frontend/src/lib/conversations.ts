import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { scopedKey, useProject } from '../project'

/** The conversation a set of suggested changes came from, if one was kept. */
export function useConversationFor(mutationId: string) {
  const { scope } = useProject()
  return useQuery({
    queryKey: scopedKey(scope, 'assist-session-for', mutationId),
    queryFn: () => api.assistSessionFor(scope, mutationId),
  })
}
