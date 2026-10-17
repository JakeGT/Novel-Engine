import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type PlanItem, type ReaderStep, type Reveal, type WorldTime } from '../api/client'
import { useStoryClock, whenText } from '../lib/storytime'
import { objectText, predicateLabel } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Loading, WriteProblem } from './ui'
import { WhenField } from './WhenField'

type Names = Map<string, string>
type FactLike = { subject_id: string; predicate: string; object: unknown }

function factSentence(fact: FactLike, names: Names): string {
  return `${names.get(fact.subject_id) ?? 'An entry'} ${predicateLabel(fact.predicate)} ${objectText(fact.object as Record<string, unknown>, names)}`
}

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
  const shown = (step?.shown ?? []).filter((item) => item.how === 'shown')
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

/** Choose which facts a scene shows the reader, plainly or as a hint. */
export function SceneRevealsDialog({ scene, onClose }: { scene: PlanItem; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const [chosen, setChosen] = useState<Record<string, Reveal['how']>>(
    Object.fromEntries((scene.reveals ?? []).map((item) => [item.fact_id, item.how ?? 'shown'])),
  )
  const [filter, setFilter] = useState('')
  const save = useCanonWrite<PlanItem>(
    (expectedRevision) =>
      api.setSceneReveals(
        scope,
        scene.id,
        Object.entries(chosen).map(([fact_id, how]) => ({ fact_id, how })),
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
          reader is not assumed to know it yet.
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
                  onChange={(event) => setChosen({ ...chosen, [fact.id]: event.target.value as Reveal['how'] })}
                >
                  <option value="shown">Shown plainly</option>
                  <option value="hinted">Only hinted</option>
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

/** A story bible a reader could see after this scene, as a file to download. */
export function ReaderGuideDialog({ scene, onClose }: { scene: PlanItem; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const [viewpoint, setViewpoint] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    setError(null)
    try {
      const guide = await api.readerGuide(scope, scene.id, viewpoint || undefined)
      const url = URL.createObjectURL(new Blob([guide.markdown], { type: 'text/markdown' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `${guide.title.replace(/[^\p{L}\p{N} ,'-]+/gu, '').trim() || 'reader guide'}.md`
      link.click()
      URL.revokeObjectURL(url)
      onClose()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title="Reader’s guide up to this scene" onClose={onClose}>
      <div className="stack">
        <p>
          Everything a reader could know after “{scene.title}”. It leaves out secrets not yet shown,
          anything dated after this scene, and anyone who has not appeared yet. It downloads as a
          plain text file (Markdown) that any editor can open.
        </p>
        <label className="field">
          <span>Whose guide</span>
          <select value={viewpoint} onChange={(event) => setViewpoint(event.target.value)}>
            <option value="">The reader’s</option>
            {characters.map((item) => (
              <option key={item.id} value={item.id}>
                As {item.name} knows it
              </option>
            ))}
          </select>
        </label>
        <ErrorNotice error={error} prefix="Could not make the guide" />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={() => void download()}>
            {busy ? 'Making it…' : 'Download the guide'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
