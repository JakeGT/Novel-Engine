import { describe, expect, it } from 'vitest'
import { fieldIdentifier, predicateIdentifier, slugify, typeIdentifier } from './identifiers'
import { dayLabel, timeAgo } from './time'
import {
  describeOperation,
  namesFromOperations,
  objectText,
  predicateLabel,
  typeLabel,
  typePlural,
  valueText,
} from './words'

describe('author-facing wording', () => {
  it('names types and predicates in plain words, including ones the project added', () => {
    expect(typePlural('Character')).toBe('Characters')
    expect(typePlural('Faculty')).toBe('Faculties')
    expect(typeLabel('WorldState')).toBe('World state')
    expect(predicateLabel('MEMBER_OF')).toBe('is a member of')
    expect(predicateLabel('TRAINED_BY')).toBe('trained by')
  })

  it('writes values without JSON punctuation where it can', () => {
    expect(valueText(true)).toBe('Yes')
    expect(valueText(['a', 'b'])).toBe('a, b')
    expect(valueText({ kind: 'sloop', guns: 4 })).toBe('kind: sloop, guns: 4')
    expect(valueText({ nested: { deep: 1 } })).toBe('{"nested":{"deep":1}}')
    expect(valueText(null)).toBe('—')
  })

  it('describes proposed changes as sentences, naming entries that do not exist yet', () => {
    const operations = [
      { operation_type: 'create_entity', payload: { id: 'tmp:a', type: 'Location', name: 'Saltmere' } },
      {
        operation_type: 'assert_fact',
        payload: {
          id: 'tmp:f',
          proposition: {
            subject_id: 'ent_mara',
            predicate: 'LOCATED_AT',
            object: { kind: 'entity_ref', entity_id: 'tmp:a' },
          },
        },
      },
      { operation_type: 'retire_entity', payload: { id: 'ent_mara' }, target_before: { name: 'Mara Venn' } },
    ]
    const names = new Map([['ent_mara', 'Mara Venn'], ...namesFromOperations(operations)])
    expect(operations.map((operation) => describeOperation(operation, names))).toEqual([
      'Add location “Saltmere”',
      'Add fact: Mara Venn is located at Saltmere',
      'Archive “Mara Venn”',
    ])
    // An entry that is not loaded is never shown as a raw id.
    expect(objectText({ kind: 'entity_ref', entity_id: 'ent_unknown' }, new Map())).not.toContain('ent_')
  })

  it('turns what an author types into valid identifiers', () => {
    expect(slugify('  The Salt Road! ')).toBe('the-salt-road')
    expect(typeIdentifier('sea ship')).toBe('SeaShip')
    expect(fieldIdentifier('Home Port')).toBe('home_port')
    expect(predicateIdentifier('was trained by')).toBe('WAS_TRAINED_BY')
  })

  it('reports times relative to now', () => {
    const now = new Date('2026-10-10T12:00:00Z')
    expect(timeAgo('2026-10-10T11:59:40Z', now.getTime())).toBe('just now')
    expect(timeAgo('2026-10-10T09:00:00Z', now.getTime())).toBe('3 hours ago')
    expect(dayLabel('2026-10-10T08:00:00Z', now)).toBe('Today')
  })
})
