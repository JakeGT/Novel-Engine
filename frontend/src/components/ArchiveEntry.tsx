import { useId, useState } from 'react'
import { api, type CanonEntity } from '../api/client'
import { useCanonWrite, useProject } from '../project'
import { Button, Dialog, WriteProblem } from './ui'

/**
 * Archiving removes an entry from the active story, so it takes two steps: the
 * author ticks an explicit acknowledgement, which is sent with the request and
 * recorded as the approval. Without it the server parks the change for review.
 */
export function ArchiveEntry({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const [open, setOpen] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const checkboxId = useId()
  const archive = useCanonWrite<CanonEntity, boolean>(
    (expectedRevision, acknowledge) =>
      api.retireEntity(scope, entity.id, expectedRevision, acknowledge),
    () => setOpen(false),
  )

  return (
    <>
      <Button variant="danger-quiet" icon="archive" onClick={() => setOpen(true)}>
        Archive
      </Button>
      {open && (
        <Dialog title={`Archive “${entity.name}”?`} onClose={() => setOpen(false)}>
          <div className="stack">
            <p>
              It leaves your active story bible and stops appearing in lists. Its history stays
              readable, and facts that mention it keep pointing at it. Archiving is not undone
              automatically.
            </p>
            <p className="check">
              <input
                id={checkboxId}
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              <label htmlFor={checkboxId}>I understand this is a significant change</label>
            </p>
            <WriteProblem write={archive} scope={scope} />
            <div className="actions end">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                disabled={archive.pending}
                onClick={() => void archive.submit(false)}
                title="Adds the archive to the review inbox instead of applying it now"
              >
                Send for review instead
              </Button>
              <Button
                variant="danger"
                disabled={!acknowledged || archive.pending}
                onClick={() => void archive.submit(true)}
              >
                Archive entry
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  )
}
