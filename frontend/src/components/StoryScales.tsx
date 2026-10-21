import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { api, type PlanItem, type ScaleProgress, type StateScale } from '../api/client'
import { useScales } from '../lib/scales'
import { scopedKey, useEntries, useProject } from '../project'
import { Badge, Button, Dialog, ErrorNotice, Loading } from './ui'

// The same limits the server keeps (backend/domain/arcs.py).
const MAX_SCALES = 8
const MAX_LEVELS = 9
const MAX_NAME = 40

const TOWARD: Record<string, { label: string; tone: 'ok' | 'warn' | 'info' | 'neutral' }> = {
  toward: { label: 'Closer to where the arc ends', tone: 'info' },
  away: { label: 'Further from where the arc ends', tone: 'warn' },
  same: { label: 'No nearer', tone: 'neutral' },
  reached: { label: 'Where the arc ends', tone: 'ok' },
}

function slug(name: string, taken: Set<string>): string {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'scale'
  let found = base
  for (let n = 2; taken.has(found); n++) found = `${base}-${n}`
  return found
}

type Row = { id: string; name: string; levels: string; ordered: boolean; one_step: boolean; about: string }

function rowOf(scale: StateScale): Row {
  return {
    id: scale.id,
    name: scale.name,
    levels: scale.levels.join('\n'),
    ordered: scale.ordered ?? true,
    one_step: scale.one_step ?? false,
    about: scale.about ?? '',
  }
}

function levelsOf(row: Row): string[] {
  return row.levels
    .split('\n')
    .map((level) => level.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
}

function problemOf(row: Row, rows: Row[]): string | null {
  if (!row.name.trim()) return 'Name this scale, or remove it.'
  if (rows.filter((other) => other.name.trim().toLowerCase() === row.name.trim().toLowerCase()).length > 1)
    return 'Another scale already has this name.'
  const levels = levelsOf(row)
  if (levels.length < 2) return 'Give it at least two levels, one per line.'
  if (levels.length > MAX_LEVELS) return `A scale can have up to ${MAX_LEVELS} levels.`
  if (levels.some((level) => level.length > MAX_NAME)) return `Keep each level to ${MAX_NAME} characters.`
  if (new Set(levels.map((level) => level.toLowerCase())).size !== levels.length) return 'Give each level its own name.'
  return null
}

/** Set up the scales the story's characters share: a name and its levels, in order or not. */
function ScalesDialog({ scales, version, onClose }: { scales: StateScale[]; version: number; onClose: () => void }) {
  const { scope } = useProject()
  const client = useQueryClient()
  const formId = useId()
  const [rows, setRows] = useState<Row[]>(() =>
    scales.length ? scales.map(rowOf) : [{ id: '', name: '', levels: '', ordered: true, one_step: false, about: '' }],
  )
  const [tried, setTried] = useState(false)
  const save = useMutation({
    mutationFn: () => {
      const taken = new Set(rows.map((row) => row.id).filter(Boolean))
      return api.setScales(scope, {
        expected_version: version,
        scales: rows.map((row) => {
          const id = row.id || slug(row.name, taken)
          taken.add(id)
          return {
            id,
            name: row.name.trim(),
            levels: levelsOf(row),
            ordered: row.ordered,
            one_step: row.ordered && row.one_step,
            about: row.about.trim(),
          }
        }),
      })
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'scales') })
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'plan') })
      onClose()
    },
  })
  const change = (index: number, next: Partial<Row>) => setRows(rows.map((row, at) => (at === index ? { ...row, ...next } : row)))
  const invalid = rows.some((row) => problemOf(row, rows) !== null)
  return (
    <Dialog title="Scales your characters share" onClose={onClose} wide>
      <form
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTried(true)
          if (!invalid) save.mutate()
        }}
      >
        <p className="muted">
          A scale is something many characters have a place on, such as trust or standing. Name its levels, one per line;
          an arc’s stages can then say where their character stands on it. Levels are words, not scores.
        </p>
        <ol className="scale-editor">
          {rows.map((row, index) => {
            const issue = tried ? problemOf(row, rows) : null
            const problemId = `${formId}-${index}-problem`
            return (
              <li key={index} className="stack tight">
                <fieldset className="stack tight">
                  <legend>Scale {index + 1}</legend>
                  <label className="field">
                    <span>Name</span>
                    <input
                      value={row.name}
                      maxLength={MAX_NAME}
                      placeholder="Trust"
                      aria-invalid={issue ? true : undefined}
                      aria-describedby={issue ? problemId : undefined}
                      onChange={(event) => change(index, { name: event.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>Levels, one per line{row.ordered ? ', from one end to the other' : ''}</span>
                    <textarea
                      rows={4}
                      value={row.levels}
                      placeholder={'None\nWary\nSome\nComplete'}
                      aria-invalid={issue ? true : undefined}
                      aria-describedby={issue ? problemId : undefined}
                      onChange={(event) => change(index, { levels: event.target.value })}
                    />
                  </label>
                  <label className="check">
                    <input type="checkbox" checked={row.ordered} onChange={(event) => change(index, { ordered: event.target.checked })} />
                    <span>The levels go in order (from less to more)</span>
                  </label>
                  {row.ordered && (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={row.one_step}
                        onChange={(event) => change(index, { one_step: event.target.checked })}
                      />
                      <span>A character moves one level at a time</span>
                    </label>
                  )}
                  <label className="field">
                    <span>What it is about (optional)</span>
                    <input value={row.about} maxLength={200} onChange={(event) => change(index, { about: event.target.value })} />
                  </label>
                  {issue && (
                    <span className="error-text" id={problemId}>
                      {issue}
                    </span>
                  )}
                  <div className="actions">
                    <Button size="sm" variant="ghost" onClick={() => setRows(rows.filter((_, at) => at !== index))} aria-label={`Remove scale ${index + 1}`}>
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
            disabled={rows.length >= MAX_SCALES}
            onClick={() => setRows([...rows, { id: '', name: '', levels: '', ordered: true, one_step: false, about: '' }])}
          >
            Add a scale
          </Button>
          {rows.length >= MAX_SCALES && <span className="muted small">A story can have up to {MAX_SCALES} scales.</span>}
        </div>
        <ErrorNotice error={save.error} prefix="Could not save the scales" />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save the scales'}
          </Button>
        </div>
        <p className="muted small">Shared by every line of the story, what-ifs included.</p>
      </form>
    </Dialog>
  )
}

/**
 * The story's shared scales on the plan, and for each, where each arc's character
 * stands scene by scene, and whether each move takes them toward where their arc ends.
 */
export function StoryScales({ progress, byId }: { progress: ScaleProgress[]; byId: Map<string, PlanItem> }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const scales = useScales()
  const [editing, setEditing] = useState(false)
  const kept = scales.data?.scales ?? []
  const shown = new Map(progress.map((item) => [item.scale.id, item]))
  return (
    <section aria-labelledby="scales-heading" className="stack">
      <div className="actions" style={{ justifyContent: 'space-between' }}>
        <div>
          <h2 id="scales-heading">Scales</h2>
          <p className="muted small">Something many characters have a place on, such as trust, and where each stands scene by scene.</p>
        </div>
        {canWrite && scales.isSuccess && (
          <Button size="sm" icon={kept.length ? 'edit' : 'plus'} onClick={() => setEditing(true)}>
            {kept.length ? 'Change the scales' : 'Add a scale'}
          </Button>
        )}
      </div>
      {scales.isPending && <Loading>Opening the scales…</Loading>}
      <ErrorNotice error={scales.error} prefix="Could not open the scales" />
      {scales.isSuccess && kept.length === 0 && (
        <p className="muted">No scales yet. Add one, then say on an arc’s stages where its character stands on it.</p>
      )}
      {kept.map((scale) => {
        const rows = shown.get(scale.id)?.rows ?? []
        return (
          <article key={scale.id} className="scale-card stack tight" aria-labelledby={`scale-${scale.id}`}>
            <h3 id={`scale-${scale.id}`} className="scale-title">
              {scale.name}
            </h3>
            <p className="small">
              {scale.ordered ? 'In order: ' : 'States: '}
              {scale.levels.join(scale.ordered ? ' → ' : ', ')}
              {scale.one_step ? ' · one level at a time' : ''}
            </p>
            {scale.about && <p className="muted small">{scale.about}</p>}
            {rows.length === 0 ? (
              <p className="muted small">No arc’s stages are on this scale yet.</p>
            ) : (
              <table className="scale-table">
                <caption className="sr-only">Where each character stands on {scale.name}</caption>
                <thead>
                  <tr>
                    <th scope="col">Whose arc</th>
                    <th scope="col">Starts</th>
                    <th scope="col">Scene by scene</th>
                    <th scope="col">Ends</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const owner = row.about.map((entityId) => entries.names.get(entityId)).find(Boolean)
                    return (
                      <tr key={row.arc_id}>
                        <th scope="row">
                          <a href={`#plan-${row.arc_id}`}>{owner ?? byId.get(row.arc_id)?.title ?? 'An arc'}</a>
                        </th>
                        <td>{row.start ?? <span className="muted">Not said</span>}</td>
                        <td>
                          {row.steps.length === 0 ? (
                            <span className="muted">No stage placed in a scene yet</span>
                          ) : (
                            <ol className="scale-steps">
                              {row.steps.map((step) => {
                                const toward = step.toward ? TOWARD[step.toward] : null
                                return (
                                  <li key={`${step.scene_id}:${step.level}`}>
                                    <a href={`#plan-${step.scene_id}`}>
                                      {byId.get(step.scene_id)?.title
                                        ? `“${byId.get(step.scene_id)?.title}”`
                                        : `Scene ${step.scene_number}`}
                                    </a>
                                    : {step.level}{' '}
                                    {toward && <Badge tone={toward.tone}>{toward.label}</Badge>}
                                  </li>
                                )
                              })}
                            </ol>
                          )}
                        </td>
                        <td>{row.target ?? <span className="muted">Not said</span>}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </article>
        )
      })}
      {editing && scales.data && (
        <ScalesDialog scales={kept} version={scales.data.version} onClose={() => setEditing(false)} />
      )}
    </section>
  )
}
