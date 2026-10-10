import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type AssistSession, type CanonEntity } from '../api/client'
import { entryPath, scopedKey, useProject } from '../project'
import { AssistFlow } from './AssistFlow'
import { EntityForm } from './EntityForm'
import { Dialog } from './ui'

interface Props {
  type?: string
  /** Fill in this existing entry with the assistant instead of adding a new one. */
  entity?: CanonEntity
  /** An unfinished conversation to carry on with. */
  resume?: AssistSession
  onClose: () => void
}

/**
 * Add to the story bible from anywhere. The assistant leads: describe it, answer
 * a few questions, review the draft. Typing everything in by hand is one click away.
 */
export function NewEntryDialog({ type, entity, resume, onClose }: Props) {
  const { scope } = useProject()
  const navigate = useNavigate()
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const [manual, setManual] = useState<{ type?: string; name: string } | null>(null)
  const types = (vocabulary.data?.entity_types ?? []).map((item) => item.name)

  const title = entity
    ? `Tell me about ${entity.name}`
    : manual
      ? 'Add to your story bible'
      : resume
        ? 'Carry on where you left off'
        : 'What would you like to add?'

  return (
    // Remounted when switching to the by-hand form, so focus moves to its first field.
    <Dialog key={manual ? 'manual' : 'assistant'} title={title} onClose={onClose} wide>
      {manual && !entity ? (
        <EntityForm
          initialType={manual.type ?? type}
          initialName={manual.name}
          onCancel={onClose}
          onSaved={(saved) => {
            onClose()
            navigate(entryPath(scope, saved.id))
          }}
        />
      ) : (
        <AssistFlow
          entity={entity}
          initialType={type}
          types={types}
          resume={resume}
          onClose={onClose}
          onManual={entity ? undefined : setManual}
        />
      )}
    </Dialog>
  )
}
