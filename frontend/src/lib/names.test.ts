import { describe, expect, it } from 'vitest'
import { defaultLimit, describeLimit, limitBody, limitIsBroken, limitProblem } from './names'

const words = { scenes: { s1: 'Dawn', s2: 'Noon', s3: 'Dusk' }, names: { tobin: 'Tobin' }, texts: { t1: 'Chapter one' } }

describe('where a name answer holds', () => {
  it('says it in words, narrowest parts together', () => {
    expect(describeLimit({}, words)).toBe('Everywhere')
    expect(describeLimit({ from_scene_id: 's2' }, words)).toBe('Only from “Noon” on')
    expect(describeLimit({ until_scene_id: 's1' }, words)).toBe('Only until “Dawn”')
    expect(describeLimit({ from_scene_id: 's1', until_scene_id: 's3' }, words)).toBe('Only from “Dawn” to “Dusk”')
    expect(describeLimit({ from_scene_id: 's2', until_scene_id: 's2' }, words)).toBe('Only in “Noon”')
    expect(describeLimit({ branch_id: 'br', viewpoint_id: 'tobin', source_id: 't1' }, words)).toBe(
      'Only in this what-if, in “Chapter one”, when seen by Tobin',
    )
    expect(describeLimit({ from_scene_id: 'gone' }, words)).toBe('Only from a scene no longer in the plan')
    expect(limitIsBroken({ from_scene_id: 'gone' }, words.scenes)).toBe(true)
    expect(limitIsBroken({ from_scene_id: 's1' }, words.scenes)).toBe(false)
  })

  it('builds the request and says what is missing first', () => {
    const order = ['s1', 's2', 's3']
    const limit = defaultLimit(false, 's2')
    expect(limitBody(limit, { inWhatIf: false })).toEqual({
      source_id: null,
      what_if_only: false,
      from_scene_id: null,
      until_scene_id: null,
      viewpoint_id: null,
    })
    expect(limitBody({ ...limit, only: 'between', fromScene: 's1', untilScene: 's3' }, { inWhatIf: false })).toMatchObject({
      from_scene_id: 's1',
      until_scene_id: 's3',
    })
    expect(limitBody({ ...limit, only: 'text' }, { textId: 't1', inWhatIf: true })).toMatchObject({
      source_id: 't1',
      what_if_only: false,
    })
    expect(limitBody(defaultLimit(true), { inWhatIf: true }).what_if_only).toBe(true)
    expect(limitProblem({ ...limit, only: 'between', fromScene: 's3', untilScene: 's1' }, order)).toBe(
      'The first scene comes after the last one. Swap them.',
    )
    expect(limitProblem({ ...defaultLimit(false), only: 'from' }, order)).toBe('Choose the first scene it holds in.')
    expect(limitProblem({ ...limit, only: 'seen_by' }, order)).toBe('Choose whose eyes the scenes are seen through.')
    expect(limitProblem(limit, order)).toBeNull()
  })
})
