import { useQueries, useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ArcReading, type ArcStage, type CanonEntity, type PlanItem } from '../api/client'
import { factSentence } from '../lib/facts'
import { readingScenes } from '../lib/plan'
import { useScales } from '../lib/scales'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Badge, Button, Dialog, ErrorNotice, Loading, WriteProblem } from './ui'

// The same limits the server keeps (backend/domain/arcs.py).
const MAX_STAGES = 9
const MAX_NAME = 60

type Tone = 'ok' | 'warn' | 'info' | 'neutral'

/** How a stage's fact stands where the stage is reached, in words and a tone. */
function factBadge(fact: string | null | undefined): { label: string; tone: Tone } | null {
  switch (fact) {
    case 'holds':
      return { label: 'True by then', tone: 'ok' }
    case 'not_then':
      return { label: 'Not true then', tone: 'warn' }
    case 'gone':
      return { label: 'No longer in the story bible', tone: 'warn' }
    case 'unclear':
      return { label: 'Cannot tell', tone: 'neutral' }
    default:
      return null
  }
}

/**
 * Every fact the facts panels already read, as short sentences, plus any fact a stage
 * is tied to that is past that first page, so a chosen fact is always named.
 */
function useFactWords(tied: (string | null | undefined)[] = []) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const listed = facts.data?.items ?? []
  const listedIds = new Set(listed.map((fact) => fact.id))
  const missing = facts.isSuccess
    ? [...new Set(tied.filter((factId): factId is string => !!factId && !listedIds.has(factId)))]
    : []
  const extra = useQueries({
    queries: missing.map((factId) => ({
      queryKey: scopedKey(scope, 'facts', 'one', factId),
      queryFn: () => api.getFact(scope, factId),
    })),
  })
  const items = [...listed, ...extra.flatMap((query) => (query.data ? [query.data] : []))]
  const byId = new Map(items.map((fact) => [fact.id, fact]))
  return {
    items,
    names: entries.names,
    text: (factId: string) => {
      const fact = byId.get(factId)
      return fact ? factSentence(fact, entries.names) : 'a fact in your story bible'
    },
  }
}

/**
 * An arc as a compact lane: each stage in order, where it is reached, and whether
 * the fact tied to it is true there. The first stage is where the character starts.
 */
export function ArcStages({
  arc,
  reading,
  sceneLink,
}: {
  arc: PlanItem
  reading?: ArcReading
  /** Where a scene's title leads: the scene in the plan, or its own page. */
  sceneLink: (scene: PlanItem) => React.ReactNode
}) {
  const { scope } = useProject()
  const stages = arc.path ?? []
  const facts = useFactWords(stages.map((stage) => stage.fact_id))
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const byId = new Map((outline.data?.items ?? []).map((item) => [item.id, item]))
  const scales = useScales()
  const scaleName = new Map((scales.data?.scales ?? []).map((scale) => [scale.id, scale.name]))
  if (stages.length === 0) return null
  return (
    <ol className="arc-path" aria-label={`Stages of “${arc.title}”, in order`}>
      {stages.map((stage, index) => {
        const read = reading?.stages[index]
        const scene = stage.scene_id ? byId.get(stage.scene_id) : undefined
        const badge = factBadge(read?.fact)
        return (
          <li key={`${index}:${stage.name}`} className={`arc-stage${scene ? '' : ' unplaced'}`}>
            <span className="arc-stage-name">{stage.name}</span>
            <span className="arc-stage-where">
              {scene ? (
                <>
                  {sceneLink(scene)}
                  {read?.scene_number ? <span className="muted"> (scene {read.scene_number})</span> : null}
                </>
              ) : stage.scene_id ? (
                'In a scene no longer in the plan'
              ) : index === 0 ? (
                'Where it starts'
              ) : (
                'Not placed in a scene yet'
              )}
            </span>
            {stage.fact_id && (
              <span className="arc-stage-fact">
                From then on: {facts.text(stage.fact_id)} {badge && <Badge tone={badge.tone}>{badge.label}</Badge>}
              </span>
            )}
            {stage.scale_id && (
              <span className="arc-stage-fact">
                On {scaleName.get(stage.scale_id) ?? 'a scale no longer in the story'}: {stage.level}
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * Write how a character changes over an arc: named stages in order, each reached in
 * a scene of the plan (or not placed yet), each optionally tied to a fact that should
 * hold from then on. With no arc yet, this starts one for ``about`` with these stages.
 */
export function ArcPathDialog({
  arc,
  about,
  onClose,
}: {
  arc?: PlanItem
  about?: CanonEntity
  onClose: () => void
}) {
  const { scope } = useProject()
  const formId = useId()
  // Each row keeps its own key, so moving it keeps its fields (and focus) with it.
  const [rows, setRows] = useState<(ArcStage & { key: number })[]>(() =>
    (arc?.path?.length ? arc.path : [{ name: '' }, { name: '' }]).map((stage, key) => ({ ...stage, key })),
  )
  const [tried, setTried] = useState(false)
  const moved = useRef<{ key: number; by: -1 | 1 } | null>(null)
  const facts = useFactWords(rows.map((row) => row.fact_id))
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scenes = readingScenes(outline.data?.items ?? [])
  const owners = arc ? arc.refs.filter((ref) => ref.role === 'about').map((ref) => ref.entity_id) : about ? [about.id] : []
  const ownerName = owners.map((owner) => facts.names.get(owner)).find(Boolean)
  // Facts about whoever the arc belongs to come first; the rest are still there to choose.
  const involves = (fact: { subject_id: string; object: unknown }) =>
    owners.includes(fact.subject_id) ||
    owners.includes(String((fact.object as { entity_id?: string } | null)?.entity_id ?? ''))
  const theirs = facts.items.filter(involves)
  const others = facts.items.filter((fact) => !involves(fact))

  const [title, setTitle] = useState(arc?.title ?? (about ? `${about.name}’s arc` : ''))
  const moveId = (key: number, by: -1 | 1) => `${formId}-stage-${key}-${by < 0 ? 'earlier' : 'later'}`
  useEffect(() => {
    const done = moved.current
    if (done === null) return
    moved.current = null
    // Keep focus on the moved stage: the same button, or the other one once it reaches an end.
    const same = document.getElementById(moveId(done.key, done.by)) as HTMLButtonElement | null
    const other = document.getElementById(moveId(done.key, done.by < 0 ? 1 : -1)) as HTMLButtonElement | null
    ;(same && !same.disabled ? same : other)?.focus()
  })

  const scales = useScales()
  const shared = scales.data?.scales ?? []
  const clean = (stages: ArcStage[]) =>
    stages.map((row) => ({
      name: row.name.trim().replace(/\s+/g, ' '),
      ...(row.scene_id ? { scene_id: row.scene_id } : {}),
      ...(row.fact_id ? { fact_id: row.fact_id } : {}),
      ...(row.scale_id && row.level ? { scale_id: row.scale_id, level: row.level } : {}),
    }))
  const cleaned = () => clean(rows)
  const save = useCanonWrite(
    (expectedRevision) =>
      arc
        ? api.setArcPath(scope, arc.id, { stages: cleaned(), expected_revision: expectedRevision })
        : api.addPlanItem(scope, {
            kind: 'arc',
            title: title.trim(),
            summary: '',
            refs: owners.map((entity_id) => ({ entity_id, role: 'about' })),
            path: cleaned(),
            expected_revision: expectedRevision,
          }),
    onClose,
  )
  const change = (index: number, next: Partial<ArcStage>) =>
    setRows(rows.map((row, at) => (at === index ? { ...row, ...next } : row)))
  const swap = (index: number, by: -1 | 1) => {
    const next = [...rows]
    ;[next[index], next[index + by]] = [next[index + by], next[index]]
    setRows(next)
    moved.current = { key: rows[index].key, by }
  }
  const names = rows.map((row) => row.name.trim().toLowerCase())
  const problem = (index: number): string | null => {
    const name = names[index]
    if (!name) return 'Name this stage, or remove it.'
    if (names.indexOf(name) !== index) return 'Another stage already has this name.'
    return null
  }
  const titleMissing = !arc && !title.trim()
  const invalid = titleMissing || rows.some((_, index) => problem(index) !== null)
  const unchanged = arc !== undefined && JSON.stringify(cleaned()) === JSON.stringify(clean(arc.path ?? []))
  const heading = arc ? `Stages of “${arc.title}”` : `Start an arc for ${about?.name ?? 'this character'}`

  return (
    <Dialog title={heading} onClose={onClose} wide>
      <form
        id={formId}
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTried(true)
          if (invalid) return
          void save.submit()
        }}
      >
        <p className="muted">
          Name where {ownerName ?? 'the character'} stands at each turn, in order, from where they start to where they
          end, and pick the scene where each is reached. A stage can also name a fact that should be true from then on.
        </p>
        {!arc && (
          <div className="stack tight">
            <label className="field">
              <span>Name of the arc</span>
              <input
                value={title}
                maxLength={200}
                aria-invalid={tried && titleMissing ? true : undefined}
                aria-describedby={tried && titleMissing ? `${formId}-title-problem` : undefined}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            {tried && titleMissing && (
              <span className="error-text" id={`${formId}-title-problem`}>
                Give the arc a name.
              </span>
            )}
          </div>
        )}
        {outline.isPending && <Loading>Opening the plan…</Loading>}
        <ErrorNotice error={outline.error} prefix="Could not open the plan's scenes" />
        <ol className="arc-editor">
          {rows.map((row, index) => {
            const label = `Stage ${index + 1}`
            const issue = tried ? problem(index) : null
            return (
              <li key={row.key} className="arc-editor-row">
                <fieldset className="stack tight">
                  <legend>
                    {label}
                    {index === 0 && <span className="muted small"> — where they start</span>}
                    {index === rows.length - 1 && index > 0 && <span className="muted small"> — where they end, for now</span>}
                  </legend>
                  <div className="stack tight">
                    <label className="field">
                      <span>Where they stand</span>
                      <input
                        value={row.name}
                        maxLength={MAX_NAME}
                        placeholder={index === 0 ? 'Trusts no one' : 'Trusts Tobin'}
                        aria-invalid={issue ? true : undefined}
                        aria-describedby={issue ? `${formId}-${index}-problem` : undefined}
                        onChange={(event) => change(index, { name: event.target.value })}
                      />
                    </label>
                    {issue && (
                      <span className="error-text" id={`${formId}-${index}-problem`}>
                        {issue}
                      </span>
                    )}
                  </div>
                  <div className="arc-editor-pickers">
                    <label className="field">
                      <span>Reached in</span>
                      <select
                        value={row.scene_id ?? ''}
                        onChange={(event) => change(index, { scene_id: event.target.value || null })}
                      >
                        <option value="">{index === 0 ? 'Where they start (no scene)' : 'Not placed yet'}</option>
                        {scenes.map((scene, at) => (
                          <option key={scene.id} value={scene.id}>
                            {at + 1}. {scene.title}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>True from then on (optional)</span>
                      <select
                        value={row.fact_id ?? ''}
                        onChange={(event) => change(index, { fact_id: event.target.value || null })}
                      >
                        <option value="">No fact</option>
                        {theirs.length > 0 && (
                          <optgroup label={`About ${ownerName ?? 'them'}`}>
                            {theirs.map((fact) => (
                              <option key={fact.id} value={fact.id}>
                                {factSentence(fact, facts.names)}
                              </option>
                            ))}
                          </optgroup>
                        )}
                        {others.length > 0 && (
                          <optgroup label="Everything else">
                            {others.map((fact) => (
                              <option key={fact.id} value={fact.id}>
                                {factSentence(fact, facts.names)}
                              </option>
                            ))}
                          </optgroup>
                        )}
                      </select>
                    </label>
                    {shared.length > 0 && (
                      <label className="field">
                        <span>On a shared scale (optional)</span>
                        <select
                          value={row.scale_id && row.level ? `${row.scale_id}\u0000${row.level}` : ''}
                          onChange={(event) => {
                            const [scaleId, level] = event.target.value ? event.target.value.split('\u0000') : [null, null]
                            change(index, { scale_id: scaleId, level })
                          }}
                        >
                          <option value="">Not on a scale</option>
                          {row.scale_id && row.level && !shared.some((scale) => scale.id === row.scale_id && scale.levels.includes(row.level ?? '')) && (
                            <option value={`${row.scale_id}\u0000${row.level}`}>{row.level} (no longer on its scale)</option>
                          )}
                          {shared.map((scale) => (
                            <optgroup key={scale.id} label={scale.name}>
                              {scale.levels.map((level) => (
                                <option key={level} value={`${scale.id}\u0000${level}`}>
                                  {scale.name}: {level}
                                </option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  <div className="actions">
                    <Button
                      id={moveId(row.key, -1)}
                      size="sm"
                      variant="ghost"
                      disabled={index === 0}
                      onClick={() => swap(index, -1)}
                      aria-label={`Move ${label} earlier`}
                    >
                      Move earlier
                    </Button>
                    <Button
                      id={moveId(row.key, 1)}
                      size="sm"
                      variant="ghost"
                      disabled={index === rows.length - 1}
                      onClick={() => swap(index, 1)}
                      aria-label={`Move ${label} later`}
                    >
                      Move later
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setRows(rows.filter((_, at) => at !== index))}
                      aria-label={`Remove ${label}`}
                    >
                      Remove
                    </Button>
                  </div>
                </fieldset>
              </li>
            )
          })}
        </ol>
        <div className="actions">
          <Button
            size="sm"
            icon="plus"
            disabled={rows.length >= MAX_STAGES}
            onClick={() => setRows([...rows, { name: '', key: Math.max(-1, ...rows.map((row) => row.key)) + 1 }])}
          >
            Add a stage
          </Button>
          {rows.length >= MAX_STAGES && <span className="muted small">An arc can have up to {MAX_STAGES} stages.</span>}
        </div>
        {scenes.length === 0 && outline.isSuccess && (
          <p className="notice plain" role="note">
            There are no scenes in the plan yet. Name the stages now and place them once the scenes exist.
          </p>
        )}
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={unchanged || save.pending}>
            {save.pending ? 'Saving…' : arc ? 'Save the stages' : 'Start the arc'}
          </Button>
        </div>
        <p className="muted small">
          A stage reached before the one above it, a fact not true where its stage is reached, and a long stretch with
          no stage are listed for you to look at. Nothing is ever stopped.
        </p>
      </form>
    </Dialog>
  )
}

/**
 * How a character changes, on their own page: each arc about them with its stages,
 * and anything worth a look. Arcs live in the plan; this is a view of them.
 */
export function CharacterArcs({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const [editing, setEditing] = useState<{ arc?: PlanItem } | null>(null)
  if (entity.type !== 'Character') return null
  const arcs = (outline.data?.items ?? []).filter(
    (item) => item.kind === 'arc' && item.refs.some((ref) => ref.role === 'about' && ref.entity_id === entity.id),
  )
  const readings = new Map((outline.data?.arcs ?? []).map((reading) => [reading.arc_id, reading]))
  const notes = (arcId: string) => (outline.data?.open ?? []).filter((entry) => entry.plan_id === arcId && entry.code.startsWith('arc_'))
  const editable = canWrite && entity.lifecycle === 'active'
  const sceneLink = (scene: PlanItem) => (
    <Link to={`/projects/${scope.projectId}/plan/${scene.id}/write`}>{scene.title}</Link>
  )

  return (
    <section className="card" aria-labelledby="arcs-heading">
      <div className="card-head">
        <div>
          <h2 id="arcs-heading">How {entity.name} changes</h2>
          <p className="muted small">
            Each arc is a path of stages through the plan. It is what you mean to happen, not a fact in the story.
          </p>
        </div>
        {editable && arcs.length > 0 && (
          <Button size="sm" icon="plus" onClick={() => setEditing({})}>
            Another arc
          </Button>
        )}
      </div>
      <ErrorNotice error={outline.error} prefix="Could not open the plan" />
      {outline.isPending && <Loading>Opening the plan…</Loading>}
      {outline.isSuccess && arcs.length === 0 && (
        <div className="stack tight">
          <p className="muted">
            No arc yet. Write where {entity.name} starts, the turns along the way, and where they end up, each in a
            scene of the plan.
          </p>
          {editable && (
            <div>
              <Button size="sm" icon="plus" onClick={() => setEditing({})}>
                Start an arc for {entity.name}
              </Button>
            </div>
          )}
        </div>
      )}
      {arcs.length > 0 && (
        <ul className="rows arc-list">
          {arcs.map((arc) => {
            const flagged = notes(arc.id)
            return (
              <li key={arc.id} className="arc-entry stack tight">
                <div className="actions" style={{ justifyContent: 'space-between' }}>
                  <h3 className="arc-title">{arc.title}</h3>
                  <span className="actions">
                    <Link className="small" to={`/projects/${scope.projectId}/plan#plan-${arc.id}`}>
                      See it in the plan
                    </Link>
                    {editable && (
                      <Button size="sm" icon="edit" onClick={() => setEditing({ arc })} aria-label={`Change the stages of ${arc.title}`}>
                        {(arc.path ?? []).length > 0 ? 'Change the stages' : 'Add stages'}
                      </Button>
                    )}
                  </span>
                </div>
                {(arc.path ?? []).length === 0 ? (
                  <p className="muted small">No stages yet.</p>
                ) : (
                  <ArcStages arc={arc} reading={readings.get(arc.id)} sceneLink={sceneLink} />
                )}
                {flagged.length > 0 && (
                  <ul className="plan-open" aria-label={`Worth a look in ${arc.title}`}>
                    {flagged.map((entry, index) => (
                      <li key={index} className={entry.kind === 'problem' ? 'problem' : undefined}>
                        {entry.kind === 'problem' && <span className="sr-only">Worth a look: </span>}
                        {entry.message}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {editing && (
        <ArcPathDialog
          key={editing.arc?.id ?? 'new'}
          arc={editing.arc}
          about={editing.arc ? undefined : entity}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  )
}
