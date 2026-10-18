import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type AssistSession, type ConditionState, type PlanItem, type PlanKind } from '../api/client'
import { ArcPathDialog, ArcStages } from '../components/ArcPath'
import { AuditDialog } from '../components/AuditDialog'
import { ClaimDialog } from '../components/ClaimDialog'
import { NextDirections, PlanAssist } from '../components/PlanAssist'
import { PlanItemForm } from '../components/PlanItemForm'
import { PlanPromises } from '../components/PlanPromises'
import { SceneConditionsDialog } from '../components/SceneConditions'
import { SceneContextDialog } from '../components/SceneContext'
import { Icon } from '../components/icons'
import {
  SceneReaderNotes,
  SceneRevealsDialog,
  SceneTimeDialog,
} from '../components/SceneReader'
import {
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
  WriteProblem,
} from '../components/ui'
import {
  CHILD_KINDS,
  OUTCOMES,
  THREAD_STEPS,
  outcomeLabel,
  outlineTree,
  siblingsOf,
  threadsOf,
  type OutlineNode,
} from '../lib/plan'
import { factSentence } from '../lib/facts'
import { claimSentence } from '../lib/knowledge'
import { planKindLabel, plural } from '../lib/words'
import { entryPath, scopedKey, useCanonWrite, useEntries, useProject } from '../project'

type Editing = { item?: PlanItem; kind: PlanKind; parentId: string | null }

/** How a scene's condition stands where the scene comes, in words and a tone. */
function conditionBadge(state: ConditionState): { label: string; tone: 'ok' | 'warn' | 'info' | 'neutral' } {
  const before = state.when === 'before'
  switch (state.status) {
    case 'met':
      return { label: before ? 'True by then' : 'Recorded', tone: 'ok' }
    case 'planned':
      return { label: 'Planned earlier', tone: 'info' }
    case 'not_met':
      if (!before) return { label: 'Not recorded yet', tone: 'neutral' }
      return state.strength === 'hard' ? { label: 'Not true yet', tone: 'warn' } : { label: 'Not true yet', tone: 'neutral' }
    case 'unchecked':
      return { label: 'Not checked', tone: 'neutral' }
    default:
      return { label: 'Cannot tell', tone: 'neutral' }
  }
}

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
  const location = useLocation()
  // Arriving from "Pick up where you left off" carries the unfinished conversation.
  const arrived = (location.state as { resume?: AssistSession } | null)?.resume
  // Arriving at one part of the plan (from a character's arcs) shows that part once it is there.
  const loaded = outline.isSuccess
  useEffect(() => {
    if (loaded && location.hash.startsWith('#plan-')) {
      document.getElementById(location.hash.slice(1))?.scrollIntoView?.({ block: 'start' })
    }
  }, [loaded, location.hash])
  const [assist, setAssist] = useState<{ about?: PlanItem; resume?: AssistSession } | null>(
    arrived ? { resume: arrived } : null,
  )
  const [next, setNext] = useState<{ about?: PlanItem } | null>(null)
  const [conditioning, setConditioning] = useState<PlanItem | null>(null)
  const [revealing, setRevealing] = useState<PlanItem | null>(null)
  const [timing, setTiming] = useState<PlanItem | null>(null)
  const [contexting, setContexting] = useState<PlanItem | null>(null)
  const [auditing, setAuditing] = useState<PlanItem | null>(null)
  const [staging, setStaging] = useState<PlanItem | null>(null)
  // What the reader has been shown, scene by scene, in reading order.
  const journey = useQuery({
    queryKey: scopedKey(scope, 'reader'),
    queryFn: () => api.readerJourney(scope),
  })
  const stepFor = (planId: string) => journey.data?.steps.find((step) => step.scene_id === planId)
  // What is said in each scene (KNW-005): kept apart from what the reader is shown as true.
  const claims = useQuery({
    queryKey: scopedKey(scope, 'claims', 'all'),
    queryFn: () => api.listClaims(scope),
  })
  const saidIn = (planId: string) =>
    (claims.data?.items ?? [])
      .filter((claim) => claim.event.claim?.scene_id === planId)
      .map((claim) => claimSentence(claim, entries.names))
  const [saying, setSaying] = useState<PlanItem | null>(null)
  const sayingAbout = saying
    ? (entries.items.find(
        (entry) => entry.lifecycle === 'active' && saying.refs.some((ref) => ref.entity_id === entry.id),
      ) ?? entries.items.find((entry) => entry.lifecycle === 'active'))
    : undefined

  const items = outline.data?.items ?? []
  const stillOpen = outline.data?.open ?? []
  // Out of order or not ready where it comes, apart from gaps still to fill in.
  const problems = stillOpen.filter((entry) => entry.kind === 'problem')
  const gaps = stillOpen.filter((entry) => entry.kind !== 'problem')
  const promises = outline.data?.promises ?? []
  const owed = promises.filter((promise) => !promise.kept_in || promise.out_of_order).length
  const clueText = (factId: string) => {
    const fact = journey.data?.facts[factId]
    return fact ? factSentence(fact, entries.names) : 'a fact no longer in the story'
  }
  const conditionsFor = (planId: string) =>
    (outline.data?.conditions ?? []).filter((entry) => entry.plan_id === planId)
  const tree = outlineTree(items)
  const threads = threadsOf(items)
  const progressOf = (threadId: string) =>
    outline.data?.threads?.find((entry) => entry.thread_id === threadId)?.steps ?? []
  const byId = new Map(items.map((item) => [item.id, item]))
  const openFor = (planId: string) => stillOpen.filter((entry) => entry.plan_id === planId)
  const under = (planId: string) => items.filter((item) => item.parent_id === planId).length
  const arcReadings = outline.data?.arcs ?? []
  /** The arcs a scene moves: whose, and the stage reached there. */
  const arcMoves = (sceneId: string) =>
    arcReadings.flatMap((reading) =>
      reading.stages
        .filter((stage) => stage.scene_id === sceneId)
        .map((stage) => ({ reading, stage, arc: byId.get(reading.arc_id) })),
    )

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
    if ((item.advances ?? []).length > 0)
      parts.push(<span key="advances">Moves along: {titles(item.advances ?? [])}</span>)
    if (item.pays_off.length > 0) parts.push(<span key="resolves">Resolves: {titles(item.pays_off)}</span>)
    for (const move of item.kind === 'scene' ? arcMoves(item.id) : []) {
      // The arc's lane crosses this scene: say whose arc moves, and to what.
      const owner = move.reading.about.map((entityId) => entries.names.get(entityId)).find(Boolean)
      parts.push(
        <span key={`arc:${move.reading.arc_id}:${move.stage.index}`}>
          Arc: {owner ? `${owner} reaches` : 'reaches'} “{move.stage.name}”{' '}
          <a href={`#plan-${move.reading.arc_id}`} className="muted">
            ({move.arc?.title ?? 'an arc'})
          </a>
        </span>,
      )
    }
    return parts.length > 0 ? <p className="plan-about">{parts}</p> : null
  }

  /** What the viewpoint character wants, what is in the way, and how it ends. */
  const intentOf = (item: PlanItem) => {
    const intent = item.intent
    if (!intent) return null
    const ends = outcomeLabel(intent.outcome)
    const hint = OUTCOMES.find((outcome) => outcome.value === intent.outcome)?.hint
    return (
      <dl className="scene-intent-card" aria-label={`What the viewpoint wants in ${item.title}`}>
        {intent.goal && (
          <div>
            <dt>Wants</dt>
            <dd>{intent.goal}</dd>
          </div>
        )}
        {intent.obstacle && (
          <div>
            <dt>In the way</dt>
            <dd>{intent.obstacle}</dd>
          </div>
        )}
        {ends && (
          <div>
            <dt>Ends</dt>
            <dd>
              {ends}
              {hint && <span className="outcome-hint"> — {hint}</span>}
            </dd>
          </div>
        )}
      </dl>
    )
  }

  // A scene is written in the studio; the way there is always in view (V1).
  const writeLink = (item: PlanItem) =>
    item.kind === 'scene' ? (
      <Link
        className="btn sm"
        to={`/projects/${scope.projectId}/plan/${item.id}/write`}
        aria-label={`${canWrite ? 'Write' : 'Read'} this scene: ${item.title}`}
      >
        <Icon name="feather" size={15} />
        {canWrite ? 'Write this scene' : 'Read this scene'}
      </Link>
    ) : null

  const tools = (item: PlanItem) => {
    if (!canWrite) return item.kind === 'scene' ? <div className="plan-tools">{writeLink(item)}</div> : null
    const beside = siblingsOf(items, item)
    const index = beside.findIndex((other) => other.id === item.id)
    const [first, ...rest] = CHILD_KINDS[item.kind]
    return (
      <div className="plan-tools">
        {writeLink(item)}
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
          <summary aria-label={`Move, plan or remove ${item.title}`}>Move, plan, remove</summary>
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
            {CHILD_KINDS[item.kind].length > 0 && (
              <Button size="sm" variant="ghost" icon="spark" onClick={() => setAssist({ about: item })}>
                Plan what goes under it
              </Button>
            )}
            <Button size="sm" variant="ghost" icon="spark" onClick={() => setNext({ about: item })}>
              What could happen from here?
            </Button>
            {item.kind !== 'scene' && item.kind !== 'thread' && (
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Audit this part of the story: ${item.title}`}
                onClick={() => setAuditing(item)}
              >
                Audit this part of the story
              </Button>
            )}
            {item.kind === 'arc' && (
              <Button size="sm" variant="ghost" onClick={() => setStaging(item)}>
                Stages of the arc
              </Button>
            )}
            {item.kind === 'scene' && (
              <Button size="sm" variant="ghost" onClick={() => setConditioning(item)}>
                Before and after
              </Button>
            )}
            {item.kind === 'scene' && (
              <Button size="sm" variant="ghost" onClick={() => setRevealing(item)}>
                What the reader learns
              </Button>
            )}
            {item.kind === 'scene' && (
              <Button size="sm" variant="ghost" onClick={() => setTiming(item)}>
                When it happens
              </Button>
            )}
            {item.kind === 'scene' && entries.items.some((entry) => entry.lifecycle === 'active') && (
              <Button size="sm" variant="ghost" onClick={() => setSaying(item)}>
                Something said here
              </Button>
            )}
            {item.kind === 'scene' && (
              <Button size="sm" variant="ghost" onClick={() => setContexting(item)}>
                What the assistant would use
              </Button>
            )}
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
          {intentOf(item)}
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
          {conditionsFor(item.id).length > 0 && (
            <ul className="plan-conditions" aria-label={`Before and after ${item.title}`}>
              {conditionsFor(item.id).map((entry, index) => {
                const badge = conditionBadge(entry)
                return (
                  <li key={index}>
                    <span className="eyebrow">
                      {entry.when === 'before' ? 'Before' : 'After'} · {entry.strength === 'soft' ? 'preferred' : 'required'}
                    </span>{' '}
                    {entry.reading} <Badge tone={badge.tone}>{badge.label}</Badge>
                    {entry.note && <span className="condition-note"> {entry.note}</span>}
                  </li>
                )
              })}
            </ul>
          )}
          {item.kind === 'arc' && (
            <ArcStages
              arc={item}
              reading={arcReadings.find((reading) => reading.arc_id === item.id)}
              sceneLink={(scene) => <a href={`#plan-${scene.id}`}>{scene.title}</a>}
            />
          )}
          {item.kind === 'arc' && canWrite && (item.path ?? []).length === 0 && (
            <p className="small">
              <button type="button" className="link-button" onClick={() => setStaging(item)}>
                Say how {refs(item, 'about').map((entityId) => entries.names.get(entityId)).find(Boolean) ?? 'the character'}{' '}
                changes, stage by stage
              </button>
            </p>
          )}
          {item.kind === 'scene' && (
            <SceneReaderNotes
              scene={item}
              step={stepFor(item.id)}
              facts={journey.data?.facts ?? {}}
              said={saidIn(item.id)}
            />
          )}
          {item.kind === 'scene' && (
            <p className="small">
              <Link to={`/projects/${scope.projectId}/plan/${item.id}/guide`}>Reader’s guide up to this scene</Link>
            </p>
          )}
          {openFor(item.id).length > 0 && (
            <ul className="plan-open" aria-label={`Still to do for ${item.title}`}>
              {openFor(item.id).map((entry, index) => (
                <li key={index} className={entry.kind === 'problem' ? 'problem' : undefined}>
                  {entry.kind === 'problem' && <span className="sr-only">Worth a look: </span>}
                  {entry.message}
                </li>
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
        {canWrite && (
          <div className="actions">
            {items.length > 0 && (
              <Button icon="spark" onClick={() => setNext({})}>
                What could happen next?
              </Button>
            )}
            <Button variant="primary" icon="chat" onClick={() => setAssist({})}>
              Plan with the assistant
            </Button>
          </div>
        )}
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
                  <Button onClick={() => setEditing({ kind: 'story', parentId: null })}>
                    Start with the premise
                  </Button>
                  <Button onClick={() => setEditing({ kind: 'arc', parentId: null })}>Add an arc</Button>
                  <Button onClick={() => setEditing({ kind: 'scene', parentId: null })}>Add a scene</Button>
                </div>
              ) : undefined
            }
          >
            You can write without one. If you like to outline, talk it through with the
            assistant, or start by hand anywhere: the premise, an arc, or a single scene you
            already know you want.
          </EmptyState>
        )}

        {items.length > 0 && (
          <>
            {stillOpen.length > 0 && (
              // Opened by itself when something is out of order, so it is seen (V1).
              <details className={`card quiet${problems.length > 0 ? ' has-problems' : ''}`} open={problems.length > 0}>
                <summary>
                  <strong>{plural(stillOpen.length, 'thing')} the plan still needs</strong>{' '}
                  <span className="muted">
                    {problems.length > 0
                      ? `— ${problems.length} out of order or not ready, ${plural(gaps.length, 'gap')} to fill when you are ready`
                      : '— gaps to fill when you are ready, not mistakes'}
                  </span>
                </summary>
                {problems.length > 0 && (
                  <div className="stack tight">
                    <h2 className="open-heading">
                      <Icon name="flag" size={16} /> Out of order or not ready
                    </h2>
                    <ul className="rows" aria-label="Out of order or not ready">
                      {problems.map((entry, index) => (
                        <li className="row" key={index}>
                          <a className="grow" href={`#plan-${entry.plan_id}`}>
                            {byId.get(entry.plan_id)?.title}
                          </a>
                          <span>{entry.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {gaps.length > 0 && (
                  <div className="stack tight">
                    {problems.length > 0 && <h2 className="open-heading">Gaps to fill when you are ready</h2>}
                    <ul className="rows" aria-label="Gaps to fill">
                      {gaps.map((entry, index) => (
                        <li className="row" key={index}>
                          <a className="grow" href={`#plan-${entry.plan_id}`}>
                            {byId.get(entry.plan_id)?.title}
                          </a>
                          <span className="muted">{entry.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </details>
            )}
            {promises.length > 0 && (
              <p className="small">
                <a href="#promises-heading">
                  {owed > 0
                    ? `${plural(owed, 'promise')} to the reader still owed`
                    : `Every promise to the reader is kept (${promises.length})`}
                </a>
              </p>
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
                    // Where it opens, moves along and resolves, in the order of the outline.
                    const steps = progressOf(thread.id)
                    return row(
                      thread,
                      steps.length > 0 && (
                        <ol className="thread-steps" aria-label={`How “${thread.title}” runs through the plan`}>
                          {steps.map((step, index) => (
                            <li key={`${step.plan_id}:${step.step}:${index}`} className={`step-${step.step}`}>
                              <span className="thread-step-name">{THREAD_STEPS[step.step] ?? step.step}</span>{' '}
                              <a href={`#plan-${step.plan_id}`}>{byId.get(step.plan_id)?.title ?? 'a part of the plan'}</a>
                            </li>
                          ))}
                        </ol>
                      ),
                    )
                  })}
                </ul>
              )}
            </section>

            <PlanPromises promises={promises} byId={byId} clueText={clueText} />
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

      {assist && (
        <PlanAssist
          key={assist.about?.id ?? assist.resume?.id ?? 'plan'}
          about={assist.about}
          resume={assist.resume}
          onClose={() => setAssist(null)}
          onManual={() => {
            const under = assist.about
            setAssist(null)
            setEditing({ kind: under ? CHILD_KINDS[under.kind][0] ?? 'scene' : 'story', parentId: under?.id ?? null })
          }}
        />
      )}
      {next && <NextDirections about={next.about} onClose={() => setNext(null)} />}
      {conditioning && (
        <SceneConditionsDialog
          key={conditioning.id}
          scene={conditioning}
          entries={entries.items}
          onClose={() => setConditioning(null)}
        />
      )}

      {revealing && <SceneRevealsDialog key={revealing.id} scene={revealing} onClose={() => setRevealing(null)} />}
      {timing && <SceneTimeDialog key={timing.id} scene={timing} onClose={() => setTiming(null)} />}
      {saying && sayingAbout && (
        <ClaimDialog key={saying.id} subject={sayingAbout} sceneId={saying.id} onClose={() => setSaying(null)} />
      )}
      {staging && <ArcPathDialog key={staging.id} arc={staging} onClose={() => setStaging(null)} />}
      {contexting && (
        <SceneContextDialog key={contexting.id} scene={contexting} onClose={() => setContexting(null)} />
      )}
      {auditing && <AuditDialog key={auditing.id} item={auditing} onClose={() => setAuditing(null)} />}

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
