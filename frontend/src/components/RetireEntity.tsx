import { useId, useState } from 'react'
import { api, type CanonEntity } from '../api/client'
import { useCanonWrite, useProject } from '../project'
import { WriteProblem } from './ui'

/**
 * Retirement is destructive, so it is a two-step action: the author must tick an
 * explicit acknowledgement, which is sent with the request and recorded as the
 * approval. Without it the server parks the change as a proposal for review.
 */
export function RetireEntity({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const [open, setOpen] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const checkboxId = useId()
  const retire = useCanonWrite<CanonEntity, boolean>(
    (expectedRevision, acknowledge) =>
      api.retireEntity(scope, entity.id, expectedRevision, acknowledge),
    () => setOpen(false),
  )

  if (!open) {
    return (
      <button type="button" className="button danger" onClick={() => setOpen(true)}>
        Retire…
      </button>
    )
  }
  return (
    <div className="notice warn" role="group" aria-label="Confirm retirement">
      <p>
        <strong>Retire “{entity.name}”?</strong> It leaves active canon and frees its slug. Its
        history stays readable, and records that mention it keep pointing at it. This cannot be
        undone automatically.
      </p>
      <p className="check">
        <input
          id={checkboxId}
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        <label htmlFor={checkboxId}>I understand this is a high-impact change</label>
      </p>
      <div className="actions">
        <button
          type="button"
          className="button danger"
          disabled={!acknowledged || retire.pending}
          onClick={() => void retire.submit(true)}
        >
          Retire entity
        </button>
        <button
          type="button"
          className="button secondary"
          disabled={retire.pending}
          onClick={() => void retire.submit(false)}
          title="Sends the retirement to the review queue instead of committing it"
        >
          Send for review instead
        </button>
        <button type="button" className="button secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      <WriteProblem write={retire} scope={scope} />
    </div>
  )
}
