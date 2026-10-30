import { describe, expect, it } from 'vitest'
import { entity, fact } from '../test/fixtures'
import { relationWords } from './relations'

const ines = entity({ id: 'ent_ines', name: 'Ines Varga', attributes: { _importance: 'main' } })
const ref = (entityId: string) => ({ kind: 'entity_ref', entity_id: entityId, project_id: 'prj_1' })

describe('relationWords', () => {
  it('says how an entry is connected to a main entry, from either side of the fact', () => {
    const words = relationWords(
      [
        fact({ id: 'f1', subject_id: 'ent_ben', predicate: 'KNOWS', object: ref('ent_ines') }),
        // The closer tie wins over the acquaintance, whichever was recorded first.
        fact({ id: 'f2', subject_id: 'ent_ben', predicate: 'RELATED_TO', object: ref('ent_ines') }),
        fact({ id: 'f3', subject_id: 'ent_ines', predicate: 'LOCATED_AT', object: ref('ent_point') }),
        fact({ id: 'f4', subject_id: 'ent_ines', predicate: 'OWNS', object: ref('ent_clock') }),
        // Ended facts and facts between two other entries say nothing here.
        fact({ id: 'f5', subject_id: 'ent_rooke', predicate: 'KNOWS', object: ref('ent_ines'), status: 'closed' }),
        fact({ id: 'f6', subject_id: 'ent_rooke', predicate: 'KNOWS', object: ref('ent_ben') }),
      ],
      [ines],
    )

    expect(Object.fromEntries(words)).toEqual({
      ent_ben: 'related to Ines Varga',
      ent_point: 'where Ines Varga is',
      ent_clock: 'belongs to Ines Varga',
    })
  })
})
