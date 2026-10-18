import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CheckProfile, type SetAssistantSettings, type StoryAssistantSettings } from '../api/client'
import { scopedKey, useProject } from '../project'

export const PROFILE_LABEL: Record<CheckProfile, string> = {
  on_request: 'Only when I ask',
  balanced: 'Balanced',
  thorough: 'Thorough',
}

export function asked(settings: StoryAssistantSettings): SetAssistantSettings {
  return {
    check_profile: settings.check_profile,
    scene_tokens: settings.scene_tokens,
    check_minutes: settings.check_minutes,
    confirm_above_tokens: settings.confirm_above_tokens ?? null,
    conversation_tokens: settings.conversation_tokens ?? null,
  }
}

/** The story's assistant settings, shared by the studio and the AI assistant page. */
export function useAssistantSettings() {
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'assistant-settings')
  const settings = useQuery({ queryKey: key, queryFn: () => api.assistantSettings(scope) })
  const save = useMutation({
    mutationFn: (body: SetAssistantSettings) => api.setAssistantSettings(scope, body),
    onSuccess: (saved) => {
      client.setQueryData(key, saved)
      // What the studio says about checking, and the estimates' limits, follow at once.
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'studio') })
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'assist-estimate') })
    },
  })
  return { settings, save }
}
