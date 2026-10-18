import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity } from '../api/client'
import { buildObject, type Entered } from '../lib/facts'
import { objectText, predicateLabel, typeLabel } from '../lib/words'
import { scopedKey, useEntries, useProject } from '../project'
import { ObjectControl } from './ObjectControl'

type Proposition = { subject_id: string; predicate: string; object: Record<string, unknown> }

/** What was chosen: an existing fact, or a statement of its own; or what is still missing. */
export type Statement =
  | { fact_id: string; proposition: Proposition; error?: undefined }
  | { fact_id?: undefined; proposition: Proposition; error?: undefined }
  | { error: string; fact_id?: undefined; proposition?: undefined }

/**
 * Choose something to tell, show or say: one of the story's facts, or a sentence that
 * may not be true. Shared by telling, seeing and recording what is said.
 */
export function useStatement(subject: CanonEntity, { factLabel, otherLabel }: { factLabel: string; otherLabel: string }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const [mode, setMode] = useState<'fact' | 'statement'>('fact')
  const [factId, setFactId] = useState('')
  const [aboutId, setAboutId] = useState(subject.id)
  const [predicateName, setPredicateName] = useState('')
  const [entered, setEntered] = useState<Entered>({ value: '', unit: '' })

  const everyone = entries.items.filter((item) => item.lifecycle === 'active')
  const about = everyone.find((item) => item.id === aboutId) ?? subject
  const predicates = (vocabulary.data?.predicates ?? []).filter((item) => item.subject_types.includes(about.type))
  const predicate = predicates.find((item) => item.predicate === predicateName)
  const targets = everyone.filter(
    (item) =>
      item.id !== about.id &&
      (!predicate || predicate.object_types.length === 0 || predicate.object_types.includes(item.type)),
  )
  const factItems = facts.data?.items ?? []
  const sentences = factItems.map((fact) => ({
    id: fact.id,
    text: `${entries.names.get(fact.subject_id) ?? 'An entry'} ${predicateLabel(fact.predicate)} ${objectText(fact.object as Record<string, unknown>, entries.names)}`,
  }))

  const statement = (): Statement => {
    if (mode === 'fact') {
      const fact = factItems.find((item) => item.id === factId)
      if (!fact) return { error: 'Choose one of your facts.' }
      return {
        fact_id: fact.id,
        proposition: {
          subject_id: fact.subject_id,
          predicate: fact.predicate,
          object: fact.object as Record<string, unknown>,
        },
      }
    }
    if (!predicate) return { error: 'Choose how the sentence continues.' }
    const built = buildObject(predicate, entered, scope.projectId)
    if (built.error || !built.object) return { error: built.error ?? 'Finish the sentence first.' }
    return { proposition: { subject_id: about.id, predicate: predicate.predicate, object: built.object } }
  }

  const picker = (
    <>
      <label className="check">
        <input type="radio" name="what" checked={mode === 'fact'} onChange={() => setMode('fact')} />
        <span>{factLabel}</span>
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
        <input type="radio" name="what" checked={mode === 'statement'} onChange={() => setMode('statement')} />
        <span>{otherLabel}</span>
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
    </>
  )

  return { picker, statement }
}
