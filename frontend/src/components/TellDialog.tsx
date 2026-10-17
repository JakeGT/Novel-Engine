import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity } from '../api/client'
import { buildObject, type Entered } from '../lib/facts'
import { objectText, plural, predicateLabel, typeLabel } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { ObjectControl } from './ObjectControl'
import { Button, Dialog, WriteProblem } from './ui'

type Told = { beliefs: number; already_knew: string[] }

/**
 * One character tells others something. Each listener comes to believe it; someone who
 * overhears believes it less firmly. Whether it was true is read from the facts.
 */
export function TellDialog({ speaker, onClose }: { speaker: CanonEntity; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const characters = entries.items.filter(
    (item) => item.lifecycle === 'active' && item.type === 'Character',
  )
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })

  const [tellerId, setTellerId] = useState(speaker.id)
  const [listeners, setListeners] = useState<string[]>([])
  const [overhearers, setOverhearers] = useState<string[]>([])
  const [mode, setMode] = useState<'fact' | 'statement'>('fact')
  const [factId, setFactId] = useState('')
  const [aboutId, setAboutId] = useState(speaker.id)
  const [predicateName, setPredicateName] = useState('')
  const [entered, setEntered] = useState<Entered>({ value: '', unit: '' })
  const [problem, setProblem] = useState<string | null>(null)
  const [told, setTold] = useState<Told | null>(null)

  const everyone = entries.items.filter((item) => item.lifecycle === 'active')
  const about = everyone.find((item) => item.id === aboutId) ?? speaker
  const predicates = (vocabulary.data?.predicates ?? []).filter((item) =>
    item.subject_types.includes(about.type),
  )
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = everyone.filter(
    (item) =>
      item.id !== about.id &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )
  const sentences = (facts.data?.items ?? []).map((fact) => ({
    id: fact.id,
    text: `${entries.names.get(fact.subject_id) ?? 'An entry'} ${predicateLabel(fact.predicate)} ${objectText(fact.object as Record<string, unknown>, entries.names)}`,
  }))

  const tell = useCanonWrite<Told>(
    (expectedRevision) =>
      api.tell(scope, {
        speaker_id: tellerId,
        listener_ids: listeners,
        overheard_by: overhearers,
        expected_revision: expectedRevision,
        ...(mode === 'fact'
          ? { fact_id: factId }
          : {
              proposition: {
                subject_id: about.id,
                predicate: predicateName,
                object: predicate
                  ? (buildObject(predicate, entered, scope.projectId).object ?? {})
                  : {},
              },
            }),
      }),
    (result) => setTold(result),
  )

  const toggle = (list: string[], set: (next: string[]) => void, id: string) =>
    set(list.includes(id) ? list.filter((item) => item !== id) : [...list, id])

  const submit = () => {
    if (listeners.length === 0) return setProblem('Choose who is told.')
    if (mode === 'fact' && !factId) return setProblem('Choose what they are told.')
    if (mode === 'statement') {
      if (!predicate) return setProblem('Choose how the sentence continues.')
      const built = buildObject(predicate, entered, scope.projectId)
      if (built.error) return setProblem(built.error)
    }
    setProblem(null)
    void tell.submit()
  }

  const teller = characters.find((item) => item.id === tellerId)?.name ?? speaker.name
  const others = characters.filter((item) => item.id !== tellerId)

  if (told) {
    const already = told.already_knew.map((id) => entries.names.get(id) ?? 'someone')
    return (
      <Dialog title="Told" onClose={onClose}>
        <div className="stack">
          <p>
            {told.beliefs > 0
              ? `${plural(told.beliefs, 'character')} now ${told.beliefs === 1 ? 'believes' : 'believe'} it. The telling is kept as an event, with who heard it.`
              : 'Nobody new believes it: everyone who heard it already did.'}
          </p>
          {already.length > 0 && <p className="muted small">Already believed it: {already.join(', ')}.</p>}
          <div className="actions end">
            <Button variant="primary" data-autofocus onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog title="Tell someone something" onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          Whoever is told comes to believe it. Whether it is true is worked out from your facts:
          tell them something that is not so, and they will believe something false.
        </p>
        <label className="field">
          <span>Who tells</span>
          <select value={tellerId} onChange={(event) => setTellerId(event.target.value)}>
            {characters.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="stack tight">
          <legend>Who is told</legend>
          {others.length === 0 && <p className="muted small">There is nobody else to tell yet.</p>}
          <div className="when-kinds">
            {others.map((item) => (
              <label key={item.id} className="check">
                <input
                  type="checkbox"
                  checked={listeners.includes(item.id)}
                  onChange={() => {
                    toggle(listeners, setListeners, item.id)
                    setOverhearers(overhearers.filter((id) => id !== item.id))
                  }}
                />
                <span>{item.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="stack tight">
          <legend>Who overhears it (optional)</legend>
          <div className="when-kinds">
            {others
              .filter((item) => !listeners.includes(item.id))
              .map((item) => (
                <label key={item.id} className="check">
                  <input
                    type="checkbox"
                    checked={overhearers.includes(item.id)}
                    onChange={() => toggle(overhearers, setOverhearers, item.id)}
                  />
                  <span>{item.name}</span>
                </label>
              ))}
          </div>
        </fieldset>
        <fieldset className="stack tight">
          <legend>What {teller} says</legend>
          <label className="check">
            <input type="radio" name="what" checked={mode === 'fact'} onChange={() => setMode('fact')} />
            <span>Something true in the story</span>
          </label>
          {mode === 'fact' && (
            <select aria-label="Which fact" value={factId} onChange={(event) => setFactId(event.target.value)}>
              <option value="">Choose…</option>
              {sentences.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.text}
                </option>
              ))}
            </select>
          )}
          <label className="check">
            <input
              type="radio"
              name="what"
              checked={mode === 'statement'}
              onChange={() => setMode('statement')}
            />
            <span>Something else, which may not be true</span>
          </label>
          {mode === 'statement' && (
            <div className="rule-line">
              <select
                aria-label="Who or what it is about"
                value={about.id}
                onChange={(event) => {
                  setAboutId(event.target.value)
                  setPredicateName('')
                  setEntered({ value: '', unit: '' })
                }}
              >
                {everyone.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} ({typeLabel(item.type)})
                  </option>
                ))}
              </select>
              <select
                aria-label="How the sentence continues"
                value={predicate ? predicateName : ''}
                onChange={(event) => {
                  setPredicateName(event.target.value)
                  setEntered({ value: '', unit: '' })
                }}
              >
                <option value="">Choose…</option>
                {predicates.map((item) => (
                  <option key={item.predicate} value={item.predicate}>
                    {predicateLabel(item.predicate)}
                  </option>
                ))}
              </select>
              {predicate && (
                <ObjectControl
                  predicate={predicate}
                  entered={entered}
                  onChange={setEntered}
                  targets={targets}
                  label={`What ${about.name} ${predicateLabel(predicate.predicate)}`}
                />
              )}
            </div>
          )}
        </fieldset>
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <WriteProblem write={tell} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={tell.pending} onClick={submit}>
            {tell.pending ? 'Telling…' : 'Tell them'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
