import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api, type KeptItem } from '../api/client'
import { KeptRules } from '../components/KeptForYou'
import { Button, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { fullDate, timeAgo } from '../lib/time'
import { describeOperation, namesFromOperations, plural } from '../lib/words'
import { scopedKey, useEntries, useProject } from '../project'

function UndoState({ item }: { item: KeptItem }) {
  const { scope } = useProject()
  if (!item.undo_mutation_id) return null
  const to = `/projects/${scope.projectId}/review/${item.undo_mutation_id}`
  if (item.undo_status === 'committed') {
    return <span className="muted small">Undone.</span>
  }
  if (item.undo_status === 'rejected') {
    return (
      <span className="muted small">
        You chose not to undo it. <Link to={to}>See the undo</Link>
      </span>
    )
  }
  return (
    <span className="small">
      Undo suggested and waiting for you. <Link to={to}>Review the undo</Link>
    </span>
  )
}

function KeptRow({ item, names }: { item: KeptItem; names: Map<string, string> }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const undo = useMutation({
    mutationFn: () => api.undoKept(scope, item.mutation_id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'kept-list') })
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'mutations') })
    },
  })
  const all = new Map([...names, ...namesFromOperations(item.operations)])
  const waiting = item.undo_mutation_id && item.undo_status !== 'rejected'
  const changes = item.operations.map((operation) => describeOperation(operation, all))
  return (
    <li className="kept-item">
      <div className="kept-item-head">
        <span className="grow">
          <strong>
            {changes[0]}
            {changes.length > 1 ? ` and ${plural(changes.length - 1, 'more change')}` : ''}
          </strong>
          <span className="muted small" style={{ display: 'block' }}>
            {item.note.replace(/\.$/, '')} ·{' '}
            <span title={fullDate(item.kept_at)}>{timeAgo(item.kept_at)}</span>
            {item.revision ? ` · version ${item.revision} of your story` : ''}
          </span>
        </span>
        {canWrite && !waiting && (
          <Button size="sm" icon="undo" disabled={undo.isPending} onClick={() => undo.mutate()}>
            {undo.isPending ? 'Suggesting…' : 'Undo'}
          </Button>
        )}
      </div>
      {changes.length > 1 && (
        <ul className="kept-changes">
          {changes.map((change, index) => (
            <li key={index}>{change}</li>
          ))}
        </ul>
      )}
      <div className="kept-item-foot">
        <Link className="small" to={`/projects/${scope.projectId}/review/${item.mutation_id}`}>
          See the whole change
        </Link>
        <UndoState item={item} />
      </div>
      <ErrorNotice error={undo.error} prefix="It could not be undone from here" />
    </li>
  )
}

/** Keep the safe ones for me: the author's rules and everything kept under them. */
export function KeptPage() {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const kept = useQuery({
    queryKey: scopedKey(scope, 'kept-list'),
    queryFn: () => api.keptForYou(scope),
    refetchOnMount: 'always',
  })
  // Looking here counts as having seen what was kept, so the review queue stops counting it.
  // Once per visit to this story's page: the scope object itself changes between renders.
  const { workspaceId, projectId, branchId } = scope
  useEffect(() => {
    if (!canWrite) return
    const story = { workspaceId, projectId, branchId }
    api
      .markKeptSeen(story)
      .then(() => client.invalidateQueries({ queryKey: scopedKey(story, 'kept-settings') }))
      .catch(() => undefined)
  }, [workspaceId, projectId, branchId, canWrite, client])
  const items = kept.data?.items ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <p className="muted small">
            <Link to={`/projects/${scope.projectId}/review`}>Review</Link>
          </p>
          <h1>Kept for you</h1>
          <p className="lede">
            Some suggestions from the assistant can go into your story without asking: only new
            things, never changes to what is there, and only kinds you have approved by hand
            before. Everything kept is listed here and can be undone.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <section aria-labelledby="kept-list-heading">
          <h2 id="kept-list-heading">Kept so far</h2>
          <ErrorNotice error={kept.error} prefix="Could not load what was kept" />
          {kept.isPending && <Loading>Loading…</Loading>}
          {kept.isSuccess && items.length === 0 && (
            <EmptyState icon="check" title="Nothing has been kept for you yet">
              Suggestions wait in Review until you decide, unless one of your rules below says to
              keep them.
            </EmptyState>
          )}
          {items.length > 0 && (
            <>
              <p className="muted small" role="status">
                {plural(items.length, 'suggestion')} kept for you
              </p>
              <ul className="card flush kept-list">
                {items.map((item) => (
                  <KeptRow key={item.mutation_id} item={item} names={entries.names} />
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="card" aria-labelledby="kept-rules-heading">
          <div className="card-head">
            <h2 id="kept-rules-heading">Your rules for this story</h2>
          </div>
          <KeptRules />
        </section>
      </div>
    </>
  )
}
