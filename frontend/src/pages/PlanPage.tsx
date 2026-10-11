import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type PlanItem, type PlanKind } from '../api/client'
import { PlanItemForm } from '../components/PlanItemForm'
import {
  Button,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
  WriteProblem,
} from '../components/ui'
import { CHILD_KINDS, outlineTree, siblingsOf, threadsOf, type OutlineNode } from '../lib/plan'
import { planKindLabel, plural } from '../lib/words'
import { entryPath, scopedKey, useCanonWrite, useEntries, useProject } from '../project'

type Editing = { item?: PlanItem; kind: PlanKind; parentId: string | null }

const addLabel = (kind: PlanKind, under: PlanItem) =>
  `Add ${kind === 'arc' ? 'an' : 'a'} ${planKindLabel(kind).toLowerCase()} under ${under.title}`

/**
 * The story plan as an outline: what the author means to happen, from the whole
 * story down to a scene, with the threads that run through it. Nothing here is
 * read as true in the story. A plan may have gaps; they are listed, never filled in.
 */
export function PlanPage() {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const outline = useQuery({
    queryKey: scopedKey(scope, 'plan'),
    queryFn: () => api.outline(scope),
  })
  const [editing, setEditing] = useState<Editing | null>(null)
  const [removing, setRemoving] = useState<PlanItem | null>(null)

  const items = outline.data?.items ?? []
  const stillOpen = outline.data?.open ?? []
  const tree = outlineTree(items)
  const threads = threadsOf(items)
  const byId = new Map(items.map((item) => [item.id, item]))
  const openFor = (planId: string) => stillOpen.filter((entry) => entry.plan_id === planId)
  const under = (planId: string) => items.filter((item) => item.parent_id === planId).length

  const move = useCanonWrite<PlanItem, { item: PlanItem; before: string | null }>((expectedRevision, target) =>
    api.movePlanItem(scope, target.item.id, {
      parent_id: target.item.parent_id ?? null,
      before_id: target.before,
      expected_revision: expectedRevision,
    }),
  )
  const remove = useCanonWrite<unknown, PlanItem>(
    (expectedRevision, item) => api.removePlanItem(scope, item.id, expectedRevision),
    () => setRemoving(null),
  )
  const shift = (item: PlanItem, by: -1 | 1) => {
    const beside = siblingsOf(items, item)
    const index = beside.findIndex((other) => other.id === item.id)
    // Moving down one place means coming just before the item two along.
    const before = by === -1 ? beside[index - 1] : beside[index + 2]
    void move.submit({ item, before: before?.id ?? null })
  }

  const entryLink = (entityId: string) => {
    const entry = entries.byId.get(entityId)
    return entry ? (
      <Link key={entityId} to={entryPath(scope, entityId)}>
        {entry.name}
      </Link>
    ) : (
      <span key={entityId}>an entry that is no longer there</span>
    )
  }
  const listed = (ids: string[]) =>
    ids.flatMap((entityId, index) => (index === 0 ? [entryLink(entityId)] : [', ', entryLink(entityId)]))
  const refs = (item: PlanItem, role: string) =>
    item.refs.filter((ref) => ref.role === role).map((ref) => ref.entity_id)
  const titles = (ids: string[]) => ids.map((planId) => byId.get(planId)?.title ?? 'a thread').join(', ')

  /** Who, where and which threads, in one line of ordinary words. */
  const about = (item: PlanItem) => {
    const parts: React.ReactNode[] = []
    const add = (label: string, ids: string[]) => {
      if (ids.length > 0) parts.push(<span key={label}>{label} {listed(ids)}</span>)
    }
    add('Seen by', refs(item, 'point_of_view'))
    add('At', refs(item, 'place'))
    add('With', refs(item, 'participant'))
    add('About', refs(item, 'about'))
    if (item.sets_up.length > 0) parts.push(<span key="opens">Opens: {titles(item.sets_up)}</span>)
    if (item.pays_off.length > 0) parts.push(<span key="resolves">Resolves: {titles(item.pays_off)}</span>)
    return parts.length > 0 ? <p className="plan-about">{parts}</p> : null
  }

  const tools = (item: PlanItem) => {
    if (!canWrite) return null
    const beside = siblingsOf(items, item)
    const index = beside.findIndex((other) => other.id === item.id)
    const [first, ...rest] = CHILD_KINDS[item.kind]
    return (
      <div className="plan-tools">
        <Button size="sm" icon="edit" aria-label={`Edit ${item.title}`} onClick={() => setEditing({ item, kind: item.kind as PlanKind, parentId: item.parent_id ?? null })}>
          Edit
        </Button>
        {/* One way to grow the outline is always in view: the next level down. */}
        {first && (
          <Button size="sm" variant="ghost" icon="plus" aria-label={addLabel(first, item)} onClick={() => setEditing({ kind: first, parentId: item.id })}>
            {planKindLabel(first)}
          </Button>
        )}
        <details className="plan-more">
          <summary aria-label={`More for ${item.title}`}>More</summary>
          <div className="actions">
            {rest.map((kind) => (
              <Button key={kind} size="sm" variant="ghost" icon="plus" aria-label={addLabel(kind, item)} onClick={() => setEditing({ kind, parentId: item.id })}>
                {planKindLabel(kind)}
              </Button>
            ))}
            <Button size="sm" variant="ghost" disabled={move.pending || index <= 0} onClick={() => shift(item, -1)}>
              Move up
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={move.pending || index < 0 || index >= beside.length - 1}
              onClick={() => shift(item, 1)}
            >
              Move down
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRemoving(item)}>
              Remove
            </Button>
          </div>
        </details>
      </div>
    )
  }

  const row = (item: PlanItem, children?: React.ReactNode) => (
    <li key={item.id} id={`plan-${item.id}`} className={`plan-item kind-${item.kind}`}>
      <div className="plan-head">
        <div className="plan-text">
          <span className="eyebrow">{planKindLabel(item.kind)}</span>
          <h3 className="plan-title">{item.title}</h3>
          {item.summary && <p className="plan-summary">{item.summary}</p>}
          {about(item)}
          {Object.keys(item.details).length > 0 && (
            <p className="plan-about">
              {Object.entries(item.details).map(([key, value]) => (
                <span key={key}>
                  {key}: {value}
                </span>
              ))}
            </p>
          )}
          {openFor(item.id).length > 0 && (
            <ul className="plan-open" aria-label={`Still to do for ${item.title}`}>
              {openFor(item.id).map((entry) => (
                <li key={entry.code}>{entry.message}</li>
              ))}
            </ul>
          )}
        </div>
        {tools(item)}
      </div>
      {children}
    </li>
  )
  const branch = (node: OutlineNode): React.ReactNode =>
    row(node.item, node.children.length > 0 ? <ul className="plan-list">{node.children.map(branch)}</ul> : null)

  const editingTitle = editing
    ? editing.item
      ? `Change this ${planKindLabel(editing.kind).toLowerCase()}`
      : `New ${planKindLabel(editing.kind).toLowerCase()}`
    : ''

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Story plan</h1>
          <p className="lede">
            What you mean to happen, from the whole story down to a scene. A plan is optional, and
            nothing in it is treated as true in your story.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {outline.isPending && <Loading>Opening the plan…</Loading>}
        <ErrorNotice error={outline.error} prefix="Could not open the plan" />
        <WriteProblem write={move} scope={scope} />

        {outline.isSuccess && items.length === 0 && (
          <EmptyState
            icon="layers"
            title="No plan yet"
            action={
              canWrite ? (
                <div className="actions">
                  <Button variant="primary" onClick={() => setEditing({ kind: 'story', parentId: null })}>
                    Start with the premise
                  </Button>
                  <Button onClick={() => setEditing({ kind: 'arc', parentId: null })}>Add an arc</Button>
                  <Button onClick={() => setEditing({ kind: 'scene', parentId: null })}>Add a scene</Button>
                </div>
              ) : undefined
            }
          >
            You can write without one. If you like to outline, start anywhere: the premise, an
            arc, or a single scene you already know you want.
          </EmptyState>
        )}

        {items.length > 0 && (
          <>
            {stillOpen.length > 0 && (
              <details className="card quiet">
                <summary>
                  <strong>{plural(stillOpen.length, 'thing')} the plan still needs</strong>{' '}
                  <span className="muted">— gaps to fill when you are ready, not mistakes</span>
                </summary>
                <ul className="rows">
                  {stillOpen.map((entry, index) => (
                    <li className="row" key={index}>
                      <a className="grow" href={`#plan-${entry.plan_id}`}>
                        {byId.get(entry.plan_id)?.title}
                      </a>
                      <span className="muted">{entry.message}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <section aria-labelledby="outline-heading" className="stack">
              <div className="actions" style={{ justifyContent: 'space-between' }}>
                <h2 id="outline-heading">Outline</h2>
                {canWrite && (
                  <span className="actions">
                    {CHILD_KINDS[''].map((kind) => (
                      <Button key={kind} size="sm" icon="plus" onClick={() => setEditing({ kind, parentId: null })}>
                        {planKindLabel(kind)}
                      </Button>
                    ))}
                  </span>
                )}
              </div>
              {tree.length === 0 ? (
                <p className="muted">Nothing in the outline yet; only threads.</p>
              ) : (
                <ul className="plan-list top">{tree.map(branch)}</ul>
              )}
            </section>

            <section aria-labelledby="threads-heading" className="stack">
              <div className="actions" style={{ justifyContent: 'space-between' }}>
                <div>
                  <h2 id="threads-heading">Threads</h2>
                  <p className="muted small">
                    Questions raised and promises made, and where each is answered or kept.
                  </p>
                </div>
                {canWrite && (
                  <Button size="sm" icon="plus" onClick={() => setEditing({ kind: 'thread', parentId: null })}>
                    Thread
                  </Button>
                )}
              </div>
              {threads.length === 0 ? (
                <p className="muted">No threads yet.</p>
              ) : (
                <ul className="plan-list top">
                  {threads.map((thread) => {
                    const opened = items.filter((item) => item.sets_up.includes(thread.id))
                    const resolved = items.filter((item) => item.pays_off.includes(thread.id))
                    return row(
                      thread,
                      (opened.length > 0 || resolved.length > 0) && (
                        <p className="plan-about">
                          {opened.length > 0 && <span>Opened in: {opened.map((item) => item.title).join(', ')}</span>}
                          {resolved.length > 0 && <span>Resolved in: {resolved.map((item) => item.title).join(', ')}</span>}
                        </p>
                      ),
                    )
                  })}
                </ul>
              )}
            </section>
          </>
        )}
      </div>

      {editing && (
        <Dialog title={editingTitle} onClose={() => setEditing(null)} wide>
          <PlanItemForm
            key={editing.item?.id ?? `${editing.kind}:${editing.parentId ?? ''}`}
            item={editing.item}
            kind={editing.kind}
            parentId={editing.parentId}
            entries={entries.items}
            threads={threads.filter((thread) => thread.id !== editing.item?.id)}
            onSaved={() => setEditing(null)}
            onCancel={() => setEditing(null)}
          />
        </Dialog>
      )}

      {removing && (
        <ConfirmDialog
          title={`Remove “${removing.title}” from the plan?`}
          confirmLabel={under(removing.id) > 0 ? 'Remove it and what is under it' : 'Remove from the plan'}
          keepLabel="Keep it"
          busy={remove.pending}
          error={remove.error}
          onConfirm={() => void remove.submit(removing)}
          onCancel={() => setRemoving(null)}
        >
          {under(removing.id) > 0
            ? `It has ${plural(under(removing.id), 'part')} directly under it, and everything under those goes too. `
            : ''}
          Your story bible is not affected: only the plan changes. It stays in the history, but
          going back does not put a removed part of the plan back by itself.
        </ConfirmDialog>
      )}
    </>
  )
}
