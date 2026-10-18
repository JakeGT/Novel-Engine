import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type PlanItem, type ReaderStep, type Reveal, type WorldTime } from '../api/client'
import { useStoryClock, whenText } from '../lib/storytime'
import { factSentence } from '../lib/facts'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Loading, WriteProblem } from './ui'
import { WhenField } from './WhenField'

type FactLike = { subject_id: string; predicate: string; object: unknown }


/** What the reader learns in a scene, the irony it creates, and when it is set. */
export function SceneReaderNotes({
  scene,
  step,
  facts,
}: {
  scene: PlanItem
  step?: ReaderStep
  facts: Record<string, FactLike>
}) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const clock = useStoryClock(scope)
  const say = (id: string) => (facts[id] ? factSentence(facts[id], entries.names) : 'something no longer in the story')
  const pov = step?.point_of_view ? entries.names.get(step.point_of_view) : undefined
  const comesOut = (step?.shown ?? []).filter((item) => item.main_reveal)
  const shown = (step?.shown ?? []).filter((item) => item.how === 'shown' && !item.main_reveal)
  const hinted = (step?.shown ?? []).filter((item) => item.how === 'hinted')
  const lines: { key: string; label: string; text: string }[] = []
  if (scene.when) {
    lines.push({
      key: 'when',
      label: 'When',
      text:
        whenText(scene.when, clock.unit, clock.eventName) +
        (step?.flashback ? ' — set earlier than the scene before it, so a flashback' : ''),
    })
  }
  if (comesOut.length > 0)
    lines.push({ key: 'out', label: 'Comes out here', text: comesOut.map((item) => say(item.fact_id)).join('; ') })
  if (shown.length > 0)
    lines.push({ key: 'shown', label: 'The reader learns', text: shown.map((item) => say(item.fact_id)).join('; ') })
  if (hinted.length > 0)
    lines.push({ key: 'hinted', label: 'Hinted at', text: hinted.map((item) => say(item.fact_id)).join('; ') })
  if (pov && step && step.irony.length > 0)
    lines.push({
      key: 'irony',
      label: 'The reader knows',
      text: `${step.irony.map(say).join('; ')} — which ${pov}, whose eyes this is seen through, does not.`,
    })
  if (pov && step && step.withheld.length > 0)
    lines.push({
      key: 'withheld',
      label: 'Kept from the reader',
      text: `${pov} knows ${step.withheld.map(say).join('; ')}, which the reader has not been shown yet.`,
    })
  if (step && step.repeated.length > 0)
    lines.push({ key: 'repeated', label: 'Shown again', text: `${step.repeated.map(say).join('; ')} was already shown in an earlier scene.` })
  if (lines.length === 0) return null
  return (
    <ul className="plan-conditions" aria-label={`The reader in ${scene.title}`}>
      {lines.map((line) => (
        <li key={line.key}>
          <span className="eyebrow">{line.label}</span> {line.text}
        </li>
      ))}
    </ul>
  )
}

type How = 'shown' | 'hinted' | 'main'

const howOf = (reveal: Reveal): How => (reveal.main_reveal ? 'main' : (reveal.how ?? 'shown'))

/** Choose which facts a scene shows the reader: plainly, as a hint, or as where it comes out. */
export function SceneRevealsDialog({ scene, onClose }: { scene: PlanItem; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const [chosen, setChosen] = useState<Record<string, How>>(
    Object.fromEntries((scene.reveals ?? []).map((item) => [item.fact_id, howOf(item)])),
  )
  const [filter, setFilter] = useState('')
  const save = useCanonWrite<PlanItem>(
    (expectedRevision) =>
      api.setSceneReveals(
        scope,
        scene.id,
        Object.entries(chosen).map(([fact_id, how]) => ({
          fact_id,
          how: how === 'hinted' ? 'hinted' : 'shown',
          main_reveal: how === 'main',
        })),
        expectedRevision,
      ),
    () => onClose(),
  )
  const items = (facts.data?.items ?? [])
    .map((fact) => ({ fact, text: factSentence(fact, entries.names) }))
    .filter((item) => !filter.trim() || item.text.toLowerCase().includes(filter.trim().toLowerCase()))

  return (
    <Dialog title={`What the reader learns in “${scene.title}”`} onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          Tick the facts this scene shows the reader. A hint counts as a clue, not as telling: the
          reader is not assumed to know it yet. Choose “Comes out here” for the scene where a fact is
          meant to come out: showing it plainly any earlier is then flagged as too early.
        </p>
        <label className="field">
          <span>Find a fact</span>
          <input value={filter} onChange={(event) => setFilter(event.target.value)} />
        </label>
        {facts.isPending && <Loading>Loading facts…</Loading>}
        <ErrorNotice error={facts.error} />
        <ul className="rows scroll-list">
          {items.map(({ fact, text }) => (
            <li className="row" key={fact.id}>
              <label className="check grow">
                <input
                  type="checkbox"
                  checked={fact.id in chosen}
                  onChange={(event) => {
                    const next = { ...chosen }
                    if (event.target.checked) next[fact.id] = 'shown'
                    else delete next[fact.id]
                    setChosen(next)
                  }}
                />
                <span className="sentence">{text}</span>
              </label>
              {fact.visibility === 'secret' && <span className="knowledge-tag secret">Secret</span>}
              {fact.id in chosen && (
                <select
                  aria-label={`How the reader learns: ${text}`}
                  style={{ width: 'auto' }}
                  value={chosen[fact.id]}
                  onChange={(event) => setChosen({ ...chosen, [fact.id]: event.target.value as How })}
                >
                  <option value="shown">Shown plainly</option>
                  <option value="hinted">Only hinted (a clue)</option>
                  <option value="main">Comes out here (the reveal)</option>
                </select>
              )}
            </li>
          ))}
        </ul>
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={save.pending} onClick={() => void save.submit()}>
            {save.pending ? 'Saving…' : 'Save what the reader learns'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

/** Say when in story time a scene happens. */
export function SceneTimeDialog({ scene, onClose }: { scene: PlanItem; onClose: () => void }) {
  const { scope } = useProject()
  const clock = useStoryClock(scope)
  const [when, setWhen] = useState<WorldTime | null>(scene.when ?? null)
  const save = useCanonWrite<PlanItem>(
    (expectedRevision) => api.setSceneTime(scope, scene.id, when, expectedRevision),
    () => onClose(),
  )
  return (
    <Dialog title={`When does “${scene.title}” happen?`} onClose={onClose}>
      <div className="stack">
        <p className="muted small">
          This is when it happens in the story, not where it comes in the book. A scene set earlier
          than the one before it is marked as a flashback.
        </p>
        <WhenField legend="When" value={when} onChange={setWhen} unit={clock.unit} events={clock.events} />
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={save.pending} onClick={() => void save.submit()}>
            {save.pending ? 'Saving…' : 'Save when it happens'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
