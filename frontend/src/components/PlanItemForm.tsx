import { useState, type FormEvent } from 'react'
import { api, type CanonEntity, type PlanItem, type PlanKind, type PlanRef, type SceneIntent } from '../api/client'
import { parseRows, toRows, type AttributeRow } from '../attributes'
import { OUTCOMES } from '../lib/plan'
import { planKindLabel, typeLabel } from '../lib/words'
import { useCanonWrite, useProject } from '../project'
import { Button, Field, WriteProblem } from './ui'

interface Props {
  /** Present when changing an item; absent when adding one. */
  item?: PlanItem
  /** The kind being added. */
  kind: PlanKind
  /** What a new item goes under, if anything. */
  parentId?: string | null
  entries: CanonEntity[]
  threads: PlanItem[]
  onSaved: (item: PlanItem) => void
  onCancel: () => void
}

const WHAT: Record<string, { summary: string; hint: string }> = {
  story: { summary: 'The premise', hint: 'What the story is about, and what is at stake.' },
  arc: { summary: 'What changes over this arc', hint: 'Where it starts, where it ends, and why it matters.' },
  beat: { summary: 'What happens', hint: 'The turn this beat makes in the story.' },
  scene: { summary: 'What happens', hint: 'What you mean to happen in this scene. Leave out what you have not decided.' },
  thread: { summary: 'The question or promise', hint: 'What the reader is waiting to find out, or was promised.' },
}

/** Several things chosen from a list, shown as removable chips. */
function PickSeveral({
  label,
  hint,
  options,
  chosen,
  onChange,
}: {
  label: string
  hint?: string
  options: { id: string; label: string }[]
  chosen: string[]
  onChange: (ids: string[]) => void
}) {
  const left = options.filter((option) => !chosen.includes(option.id))
  const name = (id: string) => options.find((option) => option.id === id)?.label ?? 'Something no longer there'
  return (
    <Field label={label} hint={hint}>
      {(props) => (
        <>
          {chosen.length > 0 && (
            <ul className="chips" aria-label={`${label}: chosen`}>
              {chosen.map((id) => (
                <li key={id} className="chip">
                  {name(id)}
                  <button
                    type="button"
                    className="chip-remove"
                    aria-label={`Remove ${name(id)}`}
                    onClick={() => onChange(chosen.filter((other) => other !== id))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          <select
            {...props}
            value=""
            disabled={left.length === 0}
            onChange={(event) => event.target.value && onChange([...chosen, event.target.value])}
          >
            <option value="">{left.length === 0 ? 'Nothing more to add' : 'Add…'}</option>
            {left.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </>
      )}
    </Field>
  )
}

/**
 * Add or change one part of the story plan by hand. Everything is optional except
 * a title: a plan is allowed to have gaps, and they are listed as things still to do.
 */
export function PlanItemForm({ item, kind, parentId, entries, threads, onSaved, onCancel }: Props) {
  const { scope } = useProject()
  const refsAs = (role: string) => (item?.refs ?? []).filter((ref) => ref.role === role).map((ref) => ref.entity_id)
  const [title, setTitle] = useState(item?.title ?? '')
  const [summary, setSummary] = useState(item?.summary ?? '')
  const [pointOfView, setPointOfView] = useState(refsAs('point_of_view')[0] ?? '')
  const [place, setPlace] = useState(refsAs('place')[0] ?? '')
  const [participants, setParticipants] = useState(refsAs('participant'))
  const [about, setAbout] = useState(refsAs('about'))
  const [setsUp, setSetsUp] = useState(item?.sets_up ?? [])
  const [paysOff, setPaysOff] = useState(item?.pays_off ?? [])
  const [advances, setAdvances] = useState(item?.advances ?? [])
  const [goal, setGoal] = useState(item?.intent?.goal ?? '')
  const [obstacle, setObstacle] = useState(item?.intent?.obstacle ?? '')
  const [outcome, setOutcome] = useState<string>(item?.intent?.outcome ?? '')
  const [rows, setRows] = useState<AttributeRow[]>(toRows(item?.details))
  const [problems, setProblems] = useState<Record<string, string>>({})

  const active = entries.filter((entry) => entry.lifecycle === 'active')
  const choice = (entry: CanonEntity) => ({ id: entry.id, label: `${entry.name} (${typeLabel(entry.type)})` })
  const characters = active.filter((entry) => entry.type === 'Character')
  const places = active.filter((entry) => entry.type === 'Location')
  const scene = kind === 'scene'
  const carriesThreads = kind === 'beat' || kind === 'scene' || kind === 'arc'

  const collect = () => {
    const refs: PlanRef[] = [
      ...(scene && pointOfView ? [{ entity_id: pointOfView, role: 'point_of_view' }] : []),
      ...(scene && place ? [{ entity_id: place, role: 'place' }] : []),
      ...(scene || kind === 'beat' ? participants.map((entity_id) => ({ entity_id, role: 'participant' })) : []),
      ...(kind === 'arc' || kind === 'thread' ? about.map((entity_id) => ({ entity_id, role: 'about' })) : []),
    ]
    const parsed = parseRows(rows)
    const details = Object.fromEntries(
      Object.entries(parsed.attributes).map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]),
    )
    return { refs, details, errors: parsed.errors }
  }

  const write = useCanonWrite<PlanItem>((expectedRevision) => {
    const { refs, details } = collect()
    const intent: SceneIntent = {
      goal: goal.trim(),
      obstacle: obstacle.trim(),
      outcome: (outcome || null) as SceneIntent['outcome'],
    }
    const decided = Boolean(intent.goal || intent.obstacle || intent.outcome)
    const fields = {
      title: title.trim(),
      summary: summary.trim(),
      refs,
      sets_up: carriesThreads ? setsUp : [],
      pays_off: carriesThreads ? paysOff : [],
      advances: carriesThreads ? advances : [],
      details,
      // Only a scene has one; an empty one clears what was there.
      ...(scene && (item || decided) ? { intent } : {}),
    }
    return item
      ? api.changePlanItem(scope, item.id, { ...fields, expected_revision: expectedRevision })
      : api.addPlanItem(scope, { kind, parent_id: parentId ?? null, ...fields, expected_revision: expectedRevision })
  }, onSaved)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const errors: Record<string, string> = { ...collect().errors }
    if (!title.trim()) errors.title = 'Give it a title.'
    setProblems(errors)
    if (Object.keys(errors).length === 0) void write.submit()
  }
  const setRow = (index: number, patch: Partial<AttributeRow>) =>
    setRows((list) => list.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  // A thread is opened, moved along or resolved here: one of the three, never two.
  const threadOptions = (except: string[]) =>
    threads.filter((thread) => !except.includes(thread.id)).map((thread) => ({ id: thread.id, label: thread.title }))
  const kindName = planKindLabel(kind).toLowerCase()

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-label={item ? `Change ${item.title}` : `New ${kindName}`}>
      <Field label="Title" error={problems.title}>
        {(props) => (
          <input {...props} data-autofocus value={title} autoComplete="off" onChange={(event) => setTitle(event.target.value)} />
        )}
      </Field>
      <Field label={`${WHAT[kind].summary} (optional)`} hint={WHAT[kind].hint}>
        {(props) => <textarea {...props} rows={4} value={summary} onChange={(event) => setSummary(event.target.value)} />}
      </Field>

      {scene && (
        <div className="grid-2">
          <Field label="Point of view (optional)" hint="Whose eyes the scene is seen through.">
            {(props) => (
              <select {...props} value={pointOfView} onChange={(event) => setPointOfView(event.target.value)}>
                <option value="">Not decided</option>
                {characters.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Place (optional)" hint="Where it happens.">
            {(props) => (
              <select {...props} value={place} onChange={(event) => setPlace(event.target.value)}>
                <option value="">Not decided</option>
                {places.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>
      )}
      {(scene || kind === 'beat') && (
        <PickSeveral
          label="Who or what is in it (optional)"
          options={active.map(choice)}
          chosen={participants}
          onChange={setParticipants}
        />
      )}
      {(kind === 'arc' || kind === 'thread') && (
        <PickSeveral
          label={kind === 'arc' ? 'Whose arc it is (optional)' : 'Who or what it is about (optional)'}
          options={active.map(choice)}
          chosen={about}
          onChange={setAbout}
        />
      )}
      {scene && (
        <fieldset className="stack tight scene-intent">
          <legend>What the viewpoint character wants here (optional)</legend>
          <div className="grid-2">
            <Field label="Wants" hint="Their goal in this scene.">
              {(props) => (
                <input {...props} value={goal} maxLength={400} autoComplete="off" onChange={(event) => setGoal(event.target.value)} />
              )}
            </Field>
            <Field label="In the way" hint="Who or what stands against it.">
              {(props) => (
                <input
                  {...props}
                  value={obstacle}
                  maxLength={400}
                  autoComplete="off"
                  onChange={(event) => setObstacle(event.target.value)}
                />
              )}
            </Field>
          </div>
          <Field
            label="How it ends for them"
            hint="“Yes, but” and “No, and” keep a story moving; a plain yes or no can let it stall."
          >
            {(props) => (
              <select {...props} value={outcome} onChange={(event) => setOutcome(event.target.value)}>
                <option value="">Not decided</option>
                {OUTCOMES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}: {option.hint}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </fieldset>
      )}
      {carriesThreads && threads.length > 0 && (
        <div className="grid-2">
          <PickSeveral
            label="Threads it opens (optional)"
            hint="A question raised or a promise made here."
            options={threadOptions(advances)}
            chosen={setsUp}
            onChange={setSetsUp}
          />
          <PickSeveral
            label="Threads it moves along (optional)"
            hint="A clue, a doubt or a promise renewed, between opening and resolving."
            options={threadOptions([...setsUp, ...paysOff])}
            chosen={advances}
            onChange={setAdvances}
          />
          <PickSeveral
            label="Threads it resolves (optional)"
            hint="A question answered or a promise kept here."
            options={threadOptions(advances)}
            chosen={paysOff}
            onChange={setPaysOff}
          />
        </div>
      )}

      <details className="more" open={rows.length > 0 || undefined}>
        <summary>Anything else to keep with it</summary>
        <fieldset className="stack tight">
          <legend className="sr-only">Notes</legend>
          <p className="hint small muted">Short notes of your own, like “stakes: the lighthouse licence”.</p>
          {rows.map((row, index) => (
            <div className="pair" key={index}>
              <label className="mini">
                <span>Note</span>
                <input aria-label={`Note ${index + 1} name`} value={row.key} onChange={(event) => setRow(index, { key: event.target.value })} />
              </label>
              <label className="mini">
                <span>Value</span>
                <input
                  aria-label={`Note ${index + 1} value`}
                  aria-invalid={problems[`row-${index}`] ? true : undefined}
                  value={row.value}
                  onChange={(event) => setRow(index, { value: event.target.value })}
                />
              </label>
              <button
                type="button"
                className="btn ghost sm"
                aria-label={`Remove note ${index + 1}`}
                onClick={() => setRows((list) => list.filter((_, i) => i !== index))}
              >
                Remove
              </button>
              {problems[`row-${index}`] && (
                <span className="field-error" role="alert">
                  {problems[`row-${index}`]}
                </span>
              )}
            </div>
          ))}
          <div>
            <Button size="sm" icon="plus" onClick={() => setRows((list) => [...list, { key: '', value: '' }])}>
              Add a note
            </Button>
          </div>
        </fieldset>
      </details>

      <WriteProblem write={write} scope={scope} />
      <div className="actions end">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" type="submit" disabled={write.pending}>
          {write.pending ? 'Saving…' : item ? 'Save changes' : `Add ${kindName} to the plan`}
        </Button>
      </div>
    </form>
  )
}
