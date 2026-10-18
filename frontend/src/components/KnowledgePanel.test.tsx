import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VOCABULARY, entity, fact } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { KnowledgePanel } from './KnowledgePanel'

afterEach(() => vi.unstubAllGlobals())

const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [], attributes: {} })
const harbor = entity({ id: 'ent_harbor', name: 'Lantern Harbor', type: 'Location', aliases: [] })
const saltmere = entity({ id: 'ent_salt', name: 'Saltmere', type: 'Location', aliases: [] })
const truth = fact()
const belief = {
  id: 'blf_1',
  holder_entity_id: 'ent_tobin',
  subject_id: 'ent_mara',
  predicate: 'LOCATED_AT',
  object: { kind: 'entity_ref', entity_id: 'ent_salt', project_id: 'prj_1' },
  confidence: 1,
  source_ref: null,
  acquisition_provenance_id: 'prov_2',
  created_revision: 3,
  closed_revision: null,
}

describe('KnowledgePanel', () => {
  it('shows what a character gets wrong, and offers to keep the truth a secret', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity(), tobin, harbor, saltmere], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin': {
        entity_id: 'ent_tobin',
        knows: [],
        wrong: [{ belief, fact: truth }],
        unconfirmed: [],
        unaware: [],
        unrecorded: 2,
      },
      'POST /facts/fct_1/amend': { ...truth, visibility: 'secret' },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<KnowledgePanel entity={tobin} />)

    const panel = await screen.findByRole('region', { name: 'What Tobin Ashe knows' })
    expect(await within(panel).findByText('Mara Venn is located at Saltmere')).toBeInTheDocument()
    expect(within(panel).getByText(/In the story: Mara Venn is located at Lantern Harbor/)).toBeInTheDocument()
    expect(within(panel).getByText(/2 other facts in the story are not recorded/)).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: 'Keep the truth a secret' }))
    await waitFor(() => expect(api.find('POST', '/facts/fct_1/amend')).toHaveLength(1))
    expect(api.find('POST', '/facts/fct_1/amend')[0].body).toEqual({
      expected_revision: 3,
      visibility: 'secret',
    })
    expect(await within(panel).findByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })

  it('suggests what each person told believes, with the reason, for review', async () => {
    const ines = entity({ id: 'ent_ines', name: 'Ines', aliases: [], attributes: {} })
    const api = mockApi({
      'GET /entities': { items: [entity(), tobin, ines, harbor, saltmere], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin': {
        entity_id: 'ent_tobin',
        knows: [],
        wrong: [],
        unconfirmed: [],
        unaware: [],
        unrecorded: 0,
      },
      'GET /facts': { items: [truth], limit: 200, offset: 0 },
      'GET /vocabulary': VOCABULARY,
      'POST /knowledge/tell': {
        mutation_id: 'mut_5',
        event_id: 'evt_1',
        beliefs: 1,
        already_knew: [],
        proposals: [
          {
            holder_id: 'ent_mara',
            how: 'told',
            confidence: 1,
            why: 'Mara Venn was told by Tobin Ashe; Mara Venn believes what people tell them, so believes it firmly.',
            already: false,
          },
          {
            holder_id: 'ent_ines',
            how: 'overheard',
            confidence: null,
            why: 'Ines overheard it but does not believe Tobin Ashe, so no belief is added.',
            already: false,
          },
        ],
      },
    })
    const user = userEvent.setup()
    renderInProject(<KnowledgePanel entity={tobin} />)
    const panel = await screen.findByRole('region', { name: 'What Tobin Ashe knows' })
    await user.click(await within(panel).findByRole('button', { name: 'Tell someone something' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tell someone something' })
    await user.click(within(dialog).getByRole('button', { name: 'Suggest what they believe' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Choose who is told.')
    const told = within(dialog).getByRole('group', { name: 'Who is told' })
    await user.click(within(told).getByRole('checkbox', { name: 'Mara Venn' }))
    const overhear = within(dialog).getByRole('group', { name: 'Who overhears it (optional)' })
    await user.click(within(overhear).getByRole('checkbox', { name: 'Ines' }))
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Which fact' }), 'fct_1')
    await user.click(within(dialog).getByRole('button', { name: 'Suggest what they believe' }))

    await waitFor(() => expect(api.find('POST', '/knowledge/tell')).toHaveLength(1))
    expect(api.find('POST', '/knowledge/tell')[0].body).toEqual({
      speaker_id: 'ent_tobin',
      listener_ids: ['ent_mara'],
      overheard_by: ['ent_ines'],
      fact_id: 'fct_1',
      expected_revision: 3,
    })
    const done = await screen.findByRole('dialog', { name: 'Suggested for review' })
    expect(within(done).getByText(/Nothing is in your story until you approve it/)).toBeInTheDocument()
    const rows = within(done).getByRole('list', { name: 'What each person would believe' })
    expect(within(rows).getByText(/so believes it firmly/)).toBeInTheDocument()
    expect(within(rows).getByText('No belief')).toBeInTheDocument()
    expect(within(rows).getByText('Overheard')).toBeInTheDocument()
    expect(within(done).getByRole('link', { name: 'Review the suggestion' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_5',
    )
  })

  it('suggests a belief for each person who sees something, leaving out who did not notice', async () => {
    const ines = entity({ id: 'ent_ines', name: 'Ines', aliases: [], attributes: {} })
    const api = mockApi({
      'GET /entities': { items: [entity(), tobin, ines, harbor, saltmere], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin': {
        entity_id: 'ent_tobin',
        knows: [],
        wrong: [],
        unconfirmed: [],
        unaware: [],
        unrecorded: 0,
      },
      'GET /facts': { items: [truth], limit: 200, offset: 0 },
      'GET /vocabulary': VOCABULARY,
      'GET /plan': {
        items: [
          {
            id: 'pln_docks',
            kind: 'scene',
            title: 'At the docks',
            summary: '',
            parent_id: null,
            position: 0,
            refs: [
              { entity_id: 'ent_mara', role: 'participant' },
              { entity_id: 'ent_ines', role: 'participant' },
              { entity_id: 'ent_harbor', role: 'location' },
            ],
            details: {},
            sets_up: [],
            pays_off: [],
            conditions: [],
            created_revision: 1,
          },
        ],
        open: [],
        conditions: [],
      },
      'POST /knowledge/witness': {
        mutation_id: 'mut_6',
        event_id: 'evt_2',
        beliefs: 1,
        already_knew: [],
        proposals: [
          { holder_id: 'ent_mara', how: 'saw', confidence: 1, why: 'Mara Venn was there and saw it happen.', already: false },
        ],
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<KnowledgePanel entity={tobin} />)
    const panel = await screen.findByRole('region', { name: 'What Tobin Ashe knows' })
    await user.click(await within(panel).findByRole('button', { name: 'Someone sees something happen' }))
    const dialog = await screen.findByRole('dialog', { name: 'Someone sees something happen' })
    await user.type(within(dialog).getByRole('textbox', { name: 'What happens' }), 'The ship comes in')
    // Choosing the scene puts everyone in it there.
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: /In which scene/ }), 'pln_docks')
    const there = within(dialog).getByRole('group', { name: 'Who is there' })
    expect(within(there).getByRole('checkbox', { name: 'Mara Venn' })).toBeChecked()
    expect(within(there).getByRole('checkbox', { name: 'Tobin Ashe' })).not.toBeChecked()
    const unnoticed = within(dialog).getByRole('group', { name: 'Who does not notice (optional)' })
    await user.click(within(unnoticed).getByRole('checkbox', { name: 'Ines' }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Which fact' }), 'fct_1')
    await expectAccessible(container)
    await user.click(within(dialog).getByRole('button', { name: 'Suggest what they believe' }))

    await waitFor(() => expect(api.find('POST', '/knowledge/witness')).toHaveLength(1))
    expect(api.find('POST', '/knowledge/witness')[0].body).toEqual({
      name: 'The ship comes in',
      present_ids: ['ent_mara', 'ent_ines'],
      not_noticed: ['ent_ines'],
      fact_id: 'fct_1',
      expected_revision: 3,
      // The scene it happens in, so what they see is placed in the story's order.
      scene_id: 'pln_docks',
    })
    const done = await screen.findByRole('dialog', { name: 'Suggested for review' })
    expect(within(done).getByText('Mara Venn was there and saw it happen.')).toBeInTheDocument()
    expect(within(done).getByText('Saw it')).toBeInTheDocument()
  })

  it('says how and when each thing was learned, and records something worked out', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity(), tobin, harbor, saltmere], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin': {
        entity_id: 'ent_tobin',
        knows: [{ fact: truth, how: 'believes' }],
        wrong: [],
        unconfirmed: [belief],
        unaware: [],
        unrecorded: 0,
        learned: [
          {
            belief_id: 'blf_0',
            fact_id: 'fct_1',
            how: 'told',
            words: 'Told by Mara Venn',
            scene_title: 'The Market',
            when: 'year 12',
            premises: [],
          },
          {
            belief_id: 'blf_1',
            how: 'worked_out',
            words: 'Worked it out',
            premises: [{ id: 'fct_1', kind: 'fact', sentence: 'Mara Venn is located at Lantern Harbor.' }],
          },
        ],
      },
      'GET /facts': { items: [truth], limit: 200, offset: 0 },
      'GET /vocabulary': VOCABULARY,
      'GET /plan': {
        items: [{ id: 'pln_docks', kind: 'scene', title: 'The Docks', refs: [], details: {} }],
        open: [],
        conditions: [],
        promises: [],
        arcs: [],
      },
      'POST /knowledge/infer': { revision: 4, event_id: 'evt_9' },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<KnowledgePanel entity={tobin} />)
    const panel = await screen.findByRole('region', { name: 'What Tobin Ashe knows' })
    expect(await within(panel).findByText('Told by Mara Venn · in “The Market” · year 12')).toBeInTheDocument()
    expect(within(panel).getByText('From: Mara Venn is located at Lantern Harbor.')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: 'Tobin Ashe works something out' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tobin Ashe works something out' })
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Which fact' }), 'fct_1')
    await user.click(within(dialog).getByRole('button', { name: 'Save what they worked out' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Choose what Tobin Ashe worked it out from.')
    const from = within(dialog).getByRole('group', { name: 'From what' })
    await user.click(within(from).getByRole('checkbox', { name: 'Mara Venn is located at Saltmere' }))
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: /In which scene/ }), 'pln_docks')
    await expectAccessible(container)
    await user.click(within(dialog).getByRole('button', { name: 'Save what they worked out' }))

    await waitFor(() => expect(api.find('POST', '/knowledge/infer')).toHaveLength(1))
    expect(api.find('POST', '/knowledge/infer')[0].body).toEqual({
      holder_id: 'ent_tobin',
      premises: ['blf_1'],
      confidence: 1,
      scene_id: 'pln_docks',
      fact_id: 'fct_1',
      expected_revision: 3,
    })
    expect(await screen.findByRole('dialog', { name: 'Saved' })).toBeInTheDocument()
  })

  it('is not shown for anything but a character', () => {
    mockApi({})
    renderInProject(<KnowledgePanel entity={harbor} />)
    expect(screen.queryByRole('region', { name: /knows/ })).not.toBeInTheDocument()
  })
})
