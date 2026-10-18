import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NameMemory, PlanItem } from '../api/client'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { NamesPage } from './NamesPage'

afterEach(() => vi.unstubAllGlobals())

const mara = entity({ id: 'ent_mara', name: 'Mara', aliases: [] })
const roe = entity({ id: 'ent_roe', name: 'Roe', aliases: [] })
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [] })

const scene = (id: string, title: string, position: number): PlanItem => ({
  id,
  kind: 'scene',
  title,
  summary: '',
  parent_id: null,
  position,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 1,
})

const memory = (): NameMemory => ({
  items: [
    {
      id: 'alb_1',
      phrase: 'the captain',
      verdict: 'is',
      entity_id: 'ent_mara',
      source_id: null,
      decided_at: '2026-10-18T09:00:00Z',
      until_scene_id: 'pln_1',
      examples: [],
    },
    {
      id: 'alb_2',
      phrase: 'the captain',
      verdict: 'is',
      entity_id: 'ent_roe',
      source_id: null,
      decided_at: '2026-10-18T09:05:00Z',
      from_scene_id: 'pln_2',
      examples: [
        {
          id: 'nex_1',
          quote: 'At dusk the captain rowed home.',
          means: true,
          scene_id: 'pln_3',
          at: [8, 19],
          holds: false,
          now: ['ent_mara'],
        },
      ],
    },
    {
      id: 'alb_3',
      phrase: 'the old man',
      verdict: 'is',
      entity_id: 'ent_tobin',
      source_id: null,
      decided_at: '2026-10-18T09:10:00Z',
      viewpoint_id: 'ent_tobin',
      examples: [],
    },
    {
      id: 'alb_4',
      phrase: 'the stranger',
      verdict: 'is',
      entity_id: 'ent_roe',
      source_id: null,
      decided_at: '2026-10-18T09:10:00Z',
      branch_id: 'br_other',
      examples: [],
    },
  ],
  names: { ent_mara: 'Mara', ent_roe: 'Roe', ent_tobin: 'Tobin Ashe' },
  scenes: { pln_1: 'Dawn', pln_2: 'Noon', pln_3: 'Dusk' },
})

const base = {
  'GET /entities': { items: [mara, roe, tobin], limit: 200, offset: 0 },
  'GET /vocabulary': VOCABULARY,
  'GET /texts': { items: [] },
  'GET /plan': { items: [scene('pln_1', 'Dawn', 0), scene('pln_2', 'Noon', 1), scene('pln_3', 'Dusk', 2)], open: [], conditions: [] },
}
const at = { path: 'names', at: 'names' }

describe('NamesPage', () => {
  it('lists each answer with where it holds, and says when an example now reads differently', async () => {
    mockApi({ ...base, 'GET /names': memory() })
    const { container } = renderInProject(<NamesPage />, at)

    const captain = (await screen.findByRole('heading', { name: '“the captain”' })).closest('section') as HTMLElement
    expect(captain).toHaveTextContent('Means Mara · Only until “Dawn”')
    expect(captain).toHaveTextContent('Means Roe · Only from “Noon” on')
    const examples = within(captain).getByRole('list', { name: 'Examples for Means Roe' })
    expect(examples).toHaveTextContent('Here it means Roe · in “Dusk”')
    expect(within(examples).getByText('Now reads as Mara')).toBeInTheDocument()
    expect(examples.querySelector('mark')).toHaveTextContent('the captain')

    const old = screen.getByRole('heading', { name: '“the old man”' }).closest('section') as HTMLElement
    expect(old).toHaveTextContent('Only when seen by Tobin Ashe')
    // Another what-if's answer does not apply here and is only counted.
    expect(screen.queryByRole('heading', { name: '“the stranger”' })).not.toBeInTheDocument()
    expect(screen.getByText(/One more answer is kept to another what-if/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('answers a name for part of the story, and checks the scenes are in order first', async () => {
    const api = mockApi({
      ...base,
      'GET /names': { items: [], names: {}, scenes: {} },
      'POST /names': reply(201, memory().items[1]),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<NamesPage />, at)

    expect(await screen.findByRole('heading', { name: 'No names answered yet' })).toBeInTheDocument()
    const form = screen.getByRole('heading', { name: 'Answer a name' }).closest('section') as HTMLElement
    await user.type(within(form).getByLabelText(/^The words/), 'the captain')
    await user.selectOptions(within(form).getByLabelText('Means'), 'ent_roe')
    await waitFor(() =>
      expect(within(form).getByRole('option', { name: 'Only between two scenes' })).toBeInTheDocument(),
    )
    await user.selectOptions(within(form).getByLabelText('Where it holds'), 'between')
    await user.selectOptions(within(form).getByLabelText('From'), 'pln_3')
    await user.selectOptions(within(form).getByLabelText('To'), 'pln_2')
    await user.click(within(form).getByRole('button', { name: 'Remember this answer' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('The first scene comes after the last one. Swap them.')
    expect(api.find('POST', '/names')).toHaveLength(0)
    await expectAccessible(container)

    await user.selectOptions(within(form).getByLabelText('From'), 'pln_2')
    await user.selectOptions(within(form).getByLabelText('To'), 'pln_3')
    await user.click(within(form).getByRole('button', { name: 'Remember this answer' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(1))
    expect(api.find('POST', '/names')[0].body).toEqual({
      phrase: 'the captain',
      verdict: 'is',
      entity_id: 'ent_roe',
      source_id: null,
      what_if_only: false,
      from_scene_id: 'pln_2',
      until_scene_id: 'pln_3',
      viewpoint_id: null,
    })
  })

  it('keeps a typed example that uses the phrase, and forgets an answer with a way back', async () => {
    const api = mockApi({
      ...base,
      'GET /names': memory(),
      'POST /names/alb_3/examples': reply(201, memory().items[2]),
      // The harness cannot send an empty 204, so an empty body stands in for it.
      'DELETE /names/alb_1': {},
      'POST /names': reply(201, { ...memory().items[0], id: 'alb_9' }),
    })
    const user = userEvent.setup()
    renderInProject(<NamesPage />, at)

    const old = (await screen.findByRole('heading', { name: '“the old man”' })).closest('section') as HTMLElement
    await user.click(within(old).getByRole('button', { name: 'Add an example' }))
    const passage = within(old).getByLabelText(/^The passage/)
    await user.type(passage, 'Nobody came.')
    expect(within(old).getByText('The passage has to use “the old man”.')).toBeInTheDocument()
    expect(within(old).getByRole('button', { name: 'Keep this example' })).toBeDisabled()
    await user.clear(passage)
    await user.type(passage, 'The old man fished alone.')
    await user.click(within(old).getByLabelText('Here it does not mean Tobin Ashe'))
    await user.click(within(old).getByRole('button', { name: 'Keep this example' }))
    await waitFor(() => expect(api.find('POST', '/names/alb_3/examples')).toHaveLength(1))
    expect(api.find('POST', '/names/alb_3/examples')[0].body).toEqual({
      quote: 'The old man fished alone.',
      means: false,
      scene_id: null,
    })

    const captain = screen.getByRole('heading', { name: '“the captain”' }).closest('section') as HTMLElement
    await user.click(within(captain).getAllByRole('button', { name: 'Forget this answer' })[0])
    await waitFor(() => expect(api.find('DELETE', '/names/alb_1')).toHaveLength(1))
    await user.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(1))
    expect(api.find('POST', '/names')[0].body).toMatchObject({ phrase: 'the captain', until_scene_id: 'pln_1' })
  })

  it('lets a reader look without changing anything', async () => {
    mockApi({ ...base, 'GET /names': memory() })
    renderInProject(<NamesPage />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByRole('heading', { name: '“the captain”' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Answer a name' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Forget this answer' })).not.toBeInTheDocument()
  })
})
