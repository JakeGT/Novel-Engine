import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type KeptSettings } from '../api/client'
import { scopedKey, useProject } from '../project'

type Choice = { kinds: string[]; allow_clients: boolean }

/** Which of the assistant's suggestions are kept without asking in this story (AST-EXP-015). */
export function useKeptSettings() {
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'kept-settings')
  const settings = useQuery({ queryKey: key, queryFn: () => api.keptSettings(scope) })
  const save = useMutation({
    mutationFn: (body: Choice) => api.setKeptSettings(scope, body),
    // A ticked box shows ticked at once; it goes back if the change is refused.
    onMutate: (body: Choice) => {
      const before = client.getQueryData<KeptSettings>(key)
      if (before) {
        client.setQueryData<KeptSettings>(key, {
          ...before,
          allow_clients: body.allow_clients,
          rules: before.rules.map((rule) => ({ ...rule, on: body.kinds.includes(rule.kind) })),
        })
      }
      return { before }
    },
    onError: (_error, _body, context) => {
      if (context?.before) client.setQueryData(key, context.before)
    },
    onSuccess: (saved) => client.setQueryData(key, saved),
  })
  return { settings, save }
}

/** The decision note a suggestion kept under the author's rule carries, as history shows it. */
export function isKeptNote(note: string | null | undefined): note is string {
  return Boolean(note?.startsWith('Kept for you under your rule'))
}
