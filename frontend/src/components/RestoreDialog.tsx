import { useMutation } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { api, type MutationCommit } from '../api/client'
import { describeOperation, namesFromOperations, plural, words } from '../lib/words'
import { useCanonWrite, useEntries, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Json, Loading, WriteProblem } from './ui'

/**
 * Going back never deletes history: it works out the changes that undo everything
 * after the chosen version and saves them as a new version.
 */
export function RestoreDialog({
  revision,
  onClose,
  onDone,
  undoing = false,
}: {
  revision: number
  onClose: () => void
  /** Called after the story has gone back, with the new version's number. */
  onDone?: (revision: number) => void
  /** Wording for taking back the one set of changes just added, rather than choosing a version. */
  undoing?: boolean
}) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const [acknowledged, setAcknowledged] = useState(false)
  const checkboxId = useId()

  const preview = useMutation({ mutationFn: () => api.previewRevert(scope, revision, null) })
  const { mutate: loadPreview } = preview
  useEffect(() => loadPreview(), [loadPreview])

  const restore = useCanonWrite<MutationCommit>(
    (expectedRevision) =>
      api.commitRevert(scope, { fromRevision: revision, toRevision: null, expectedRevision }),
    (result) => {
      onDone?.(result.revision)
      onClose()
    },
  )
  const proposal = preview.data
  const operations = (proposal?.operations ?? []).map((payload) => ({
    operation_type: String(payload.op ?? ''),
    payload,
  }))
  const names = new Map([...entries.names, ...namesFromOperations(operations)])
  const manual = proposal?.changes.filter((change) => change.inverse_operation === null) ?? []
  const possible = Boolean(proposal) && proposal?.conflicts.length === 0 && operations.length > 0

  return (
    <Dialog title={undoing ? 'Undo these changes?' : `Go back to version ${revision}?`} onClose={onClose} wide>
      <div className="stack">
        <p className="muted">
          {undoing
            ? 'Nothing is deleted. The changes below take back what was just added, and are saved as a new version — so you can always come forward again.'
            : `Nothing is deleted. The changes below undo everything after version ${revision}, and are saved as a new version — so you can always come forward again.`}
        </p>
        {preview.isPending && <Loading>Working out what would change…</Loading>}
        <ErrorNotice error={preview.error} prefix="Could not work out the changes" />

        {proposal && (
          <div className="stack" role="region" aria-label="What going back would do">
            <p role="status">
              <strong>
                {operations.length === 0
                  ? 'There is nothing to undo.'
                  : `Going back would make ${plural(operations.length, 'change')}.`}
              </strong>
            </p>
            {proposal.conflicts.length > 0 && (
              <div className="notice danger" role="alert">
                <p>
                  <strong>This version can’t be restored yet.</strong> Sort these out first:
                </p>
                <ul>
                  {proposal.conflicts.map((conflict, index) => (
                    <li key={index}>• {conflict}</li>
                  ))}
                </ul>
              </div>
            )}
            {operations.length > 0 && (
              <div>
                <p className="label">What going back would do</p>
                <ul className="rows" style={{ marginTop: '0.5rem' }}>
                  {operations.map((operation, index) => (
                    <li className="row" key={index}>
                      {describeOperation(operation, names)}
                    </li>
                  ))}
                </ul>
                <details className="more" style={{ marginTop: '0.75rem' }}>
                  <summary>Show the stored changes</summary>
                  <Json value={proposal.operations} />
                </details>
              </div>
            )}
            {manual.length > 0 && (
              <div className="notice warn" role="note">
                <p>
                  <strong>These can’t be undone automatically</strong> and would need fixing by hand:
                </p>
                <ul>
                  {manual.map((change, index) => (
                    <li key={index}>
                      • Version {change.revision}: {words(change.operation_type).toLowerCase()} —{' '}
                      {change.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!canWrite && (
              <p className="notice plain" role="note">
                You have read-only access, so you can look at this but not restore it.
              </p>
            )}
            {canWrite && possible && (
              <p className="check">
                <input
                  id={checkboxId}
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
                <label htmlFor={checkboxId}>I understand this changes a lot of my story at once</label>
              </p>
            )}
            <WriteProblem write={restore} scope={scope} />
          </div>
        )}

        <div className="actions end">
          {canWrite && possible && !acknowledged && (
            <span className="hint">{undoing ? 'Tick the box to undo.' : 'Tick the box to go back.'}</span>
          )}
          <Button data-autofocus variant="ghost" onClick={onClose}>
            {undoing ? 'Keep the changes' : 'Stay on this version'}
          </Button>
          {canWrite && possible && (
            <Button
              variant="danger"
              icon="undo"
              disabled={!acknowledged || restore.pending}
              onClick={() => void restore.submit()}
            >
              {restore.pending ? 'Restoring…' : undoing ? 'Undo these changes' : `Go back to version ${revision}`}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}
