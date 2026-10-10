// How an entry is connected to the story's main entries, read from the facts
// themselves. Unlike a tag someone typed, this is always up to date: end the fact
// and the words go with it.
import { useQueries } from '@tanstack/react-query'
import { api, type CanonEntity, type Fact, type Scope } from '../api/client'
import { scopedKey } from '../project'
import { importanceOf } from './marks'
import { predicateLabel } from './words'

/** No more than this many main entries are followed, so a list costs a few requests. */
const MAX_MAIN = 4

// Said from the other entry's side, where "X <verb> it" would read badly.
const FROM_MAIN: Record<string, (main: string) => string> = {
  LOCATED_AT: (main) => `where ${main} is`,
  OWNS: (main) => `belongs to ${main}`,
  MEMBER_OF: (main) => `${main} is a member`,
  KNOWS: (main) => `known to ${main}`,
  RELATED_TO: (main) => `related to ${main}`,
  PARTICIPATED_IN: (main) => `${main} took part`,
}

// A family tie says more than an acquaintance; the first found in this order is shown.
const ORDER = ['RELATED_TO', 'MEMBER_OF', 'OWNS', 'LOCATED_AT', 'PARTICIPATED_IN', 'KNOWS']
const rank = (predicate: string) => {
  const index = ORDER.indexOf(predicate)
  return index === -1 ? ORDER.length : index
}

export function relationWords(facts: Fact[], mains: CanonEntity[]): Map<string, string> {
  const main = new Map(mains.map((item) => [item.id, item.name]))
  const best = new Map<string, { rank: number; words: string }>()
  const offer = (entryId: string, predicate: string, words: string) => {
    const current = best.get(entryId)
    if (!main.has(entryId) && (!current || rank(predicate) < current.rank)) {
      best.set(entryId, { rank: rank(predicate), words })
    }
  }
  for (const fact of facts) {
    const object = fact.object as { kind?: string; entity_id?: string }
    if (fact.status !== 'asserted' || object.kind !== 'entity_ref' || !object.entity_id) continue
    const label = predicateLabel(fact.predicate).replace(/^is /, '')
    if (main.has(object.entity_id)) {
      offer(fact.subject_id, fact.predicate, `${label} ${main.get(object.entity_id)}`)
    }
    if (main.has(fact.subject_id)) {
      const name = main.get(fact.subject_id) as string
      const say = FROM_MAIN[fact.predicate]
      offer(object.entity_id, fact.predicate, say ? say(name) : `${name} ${predicateLabel(fact.predicate)} it`)
    }
  }
  return new Map([...best].map(([entryId, found]) => [entryId, found.words]))
}

/** For each entry, a few words on how it is connected to a main entry, if it is. */
export function useRelations(scope: Scope, entries: CanonEntity[]): Map<string, string> {
  const mains = entries
    .filter((item) => item.lifecycle === 'active' && importanceOf(item) === 'main')
    .slice(0, MAX_MAIN)
  const results = useQueries({
    queries: mains.flatMap((item) => [
      {
        queryKey: scopedKey(scope, 'facts', 'subject', item.id),
        queryFn: () => api.listFacts(scope, { subject_id: item.id, limit: 200 }),
      },
      {
        queryKey: scopedKey(scope, 'facts', 'object', item.id),
        queryFn: () => api.listFacts(scope, { object_entity_id: item.id, limit: 200 }),
      },
    ]),
  })
  return relationWords(
    results.flatMap((result) => result.data?.items ?? []),
    mains,
  )
}
