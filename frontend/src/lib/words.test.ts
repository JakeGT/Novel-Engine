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

describe('changes to an existing fact name the fact', () => {
  const names = new Map([
    ['ent_ida', 'Ida Marsh'],
    ['ent_weir', 'Fennick Weir'],
  ])
  const before = {
    proposition: {
      subject_id: 'ent_ida',
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: 'ent_weir' },
    },
  }

  it('says outright when an entry becomes another type', () => {
    expect(
      describeOperation(
        { operation_type: 'update_entity', payload: { id: 'ent_guild', type: 'Character' }, target_before: { name: 'Harbor Guild' } },
        names,
      ),
    ).toBe('Change “Harbor Guild” to a character')
    expect(
      describeOperation(
        { operation_type: 'update_entity', payload: { id: 'ent_guild', type: 'Object' }, target_before: { name: 'Harbor Guild' } },
        names,
      ),
    ).toBe('Change “Harbor Guild” to an object')
  })

  it('says which fact ends or changes when', () => {
    expect(
      describeOperation({ operation_type: 'close_fact', payload: { id: 'fct_1' }, target_before: before }, names),
    ).toMatch(/^End a fact: Ida Marsh .* Fennick Weir$/)
    expect(
      describeOperation(
        { operation_type: 'amend_fact', payload: { id: 'fct_1', ends: { kind: 'at', at: 3 } }, target_before: before },
        names,
      ),
    ).toMatch(/^Say when a fact is true: Ida Marsh .* Fennick Weir$/)
  })

  it('keeps the plain words when the fact is not known', () => {
    expect(describeOperation({ operation_type: 'close_fact', payload: { id: 'fct_1' } }, names)).toBe('End a fact')
  })
})
