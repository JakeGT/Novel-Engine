import { useNavigate } from 'react-router-dom'
import { entryPath, useProject } from '../project'
import { EntityForm } from './EntityForm'
import { Dialog } from './ui'

/** Add an entry from anywhere in the project, then land on it. */
export function NewEntryDialog({ type, onClose }: { type?: string; onClose: () => void }) {
  const { scope } = useProject()
  const navigate = useNavigate()
  return (
    <Dialog title="Add to your story bible" onClose={onClose} wide>
      <EntityForm
        initialType={type}
        onCancel={onClose}
        onSaved={(saved) => {
          onClose()
          navigate(entryPath(scope, saved.id))
        }}
      />
    </Dialog>
  )
}
