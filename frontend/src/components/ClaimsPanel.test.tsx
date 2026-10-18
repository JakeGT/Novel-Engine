import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Claim } from '../api/client'
import { VOCABULARY, entity, fact } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { ClaimsPanel } from './ClaimsPanel'

afterEach(() => vi.unstubAllGlobals())

const mara = entity()
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [], attributes: {} })
const harbor = entity({ id: 'ent_harbor', name: 'Lantern Harbor', type: 'Location', aliases: [] })
const saltmere = entity({ id: 'ent_salt', name: 'Saltmere', type: 'Location', aliases: [] })
const everyone = { items: [mara, tobin, harbor, saltmere], limit: 200, offset: 0 }

function claim(overrides: Partial<Claim> & { modality?: string; place?: string; scene?: string | null }): Claim {
  const { modality = 'states', place = 'ent_salt', scene = null, ...rest } = overrides
  return {
    event: {
      id: `evt_${modality}_${place}`,
      kind: 'claim',
      name: 'stored name',
      participants: [],
      premises: [],
      location_ids: [],
      cause_ids: [],
      outcome_ids: [],
      provenance_id: 'prov_1',
      created_revision: 2,
      claim: {
        subject_id: 'ent_mara',
        predicate: 'LOCATED_AT',
        object: { kind: 'entity_ref', entity_id: place, project_id: 'prj_1' },
        modality,
        scene_id: scene,
      },
    },
    speaker_id: null,
    speaker_role: null,
    perceiver_ids: [],
    standing: 'unconfirmed',
    ...rest,
  }
}

const outline = {
  items: [
    {
      id: 'pln_docks',
      kind: 'scene',
      title: 'At the docks',
      summary: '',
      parent_id: null,
      position: 0,
      refs: [],
      details: {},
      sets_up: [],
      pays_off: [],
      conditions: [],
      created_revision: 1,
    },
  ],
  open: [],
  conditions: [],
}

describe('ClaimsPanel', () => {
  it('shows what is said beside the facts, marked as said, and makes one a fact only through review', async () => {
    const api = mockApi({
      'GET /entities': everyone,
      'GET /plan': outline,
      'GET /knowledge/claims': {
        items: [
          claim({
            speaker_id: 'ent_tobin',
            speaker_role: 'speaker',
            perceiver_ids: ['ent_mara'],
            standing: 'contradicted',
            scene: 'pln_docks',
          }),
          claim({ modality: 'denies', place: 'ent_harbor', speaker_id: 'ent_mara', speaker_role: 'narrator', standing: 'contradicted' }),
          claim({ modality: 'wonders', place: 'ent_harbor', standing: 'borne_out' }),
        ],
      },
      'POST /knowledge/claims/evt_states_ent_salt/promote': { mutation_id: 'mut_9' },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<ClaimsPanel entity={mara} />)

    const panel = await screen.findByRole('region', { name: 'Said in the story' })
    expect(within(panel).getByText(/Said, not necessarily true/)).toBeInTheDocument()
    expect(await within(panel).findByText('Tobin Ashe says Mara Venn is located at Saltmere')).toBeInTheDocument()
    expect(within(panel).getByText('In “At the docks” · Heard by Mara Venn')).toBeInTheDocument()
    expect(
      within(panel).getByText('Mara Venn says it is not so that Mara Venn is located at Lantern Harbor'),
    ).toBeInTheDocument()
    expect(within(panel).getByText('As narrator, perhaps unreliably')).toBeInTheDocument()
    expect(within(panel).getByText('The narration wonders whether Mara Venn is located at Lantern Harbor')).toBeInTheDocument()
    expect(within(panel).getAllByText('Your facts say otherwise')).toHaveLength(2)
    expect(within(panel).getByText('Your facts agree')).toBeInTheDocument()
    expect(within(panel).getAllByText('Said')).toHaveLength(3)
    // A denial, or something the facts already say, has nothing to make a fact of.
    expect(within(panel).getAllByRole('button', { name: /^Make it a fact/ })).toHaveLength(1)
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: /^Make it a fact: Tobin Ashe says/ }))
    await waitFor(() => expect(api.find('POST', '/promote')).toHaveLength(1))
    expect(api.find('POST', '/promote')[0].body).toEqual({ expected_revision: 3 })
    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_9'),
    )
  })

  it('records something a character says, and can suggest what its hearers believe', async () => {
    const api = mockApi({
      'GET /entities': everyone,
      'GET /plan': outline,
      'GET /knowledge/claims': { items: [] },
      'GET /facts': { items: [fact()], limit: 200, offset: 0 },
      'GET /vocabulary': VOCABULARY,
      'POST /knowledge/claims': {
        revision: 4,
        event_id: 'evt_1',
        beliefs: {
          mutation_id: 'mut_2',
          event_id: 'evt_1',
          beliefs: 1,
          already_knew: [],
          proposals: [
            {
              holder_id: 'ent_tobin',
              how: 'told',
              confidence: 0.5,
              why: 'Tobin Ashe was told by Mara Venn; Tobin Ashe believes what people tell them, so believes it firmly. It was only suggested, so less firmly again.',
              already: false,
            },
          ],
        },
      },
    })
    const user = userEvent.setup()
    renderInProject(<ClaimsPanel entity={mara} />)
    const panel = await screen.findByRole('region', { name: 'Said in the story' })
    expect(await within(panel).findByText('Nothing recorded yet.')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Record something said' }))

    const dialog = await screen.findByRole('dialog', { name: 'Record something said' })
    expect(within(dialog).getByRole('combobox', { name: 'Who says it' })).toHaveValue('ent_mara')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: /What Mara Venn does/ }), 'suggests')
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Which fact' }), 'fct_1')
    // Hearers can be suggested beliefs only once someone hears it.
    expect(within(dialog).queryByRole('checkbox', { name: /Also suggest/ })).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: 'Tobin Ashe' }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: /In which scene/ }), 'pln_docks')
    await user.click(within(dialog).getByRole('checkbox', { name: /Also suggest what those who hear it/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Record what is said' }))

    await waitFor(() => expect(api.find('POST', '/knowledge/claims')).toHaveLength(1))
    expect(api.find('POST', '/knowledge/claims')[0].body).toEqual({
      proposition: {
        subject_id: 'ent_mara',
        predicate: 'LOCATED_AT',
        object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
      },
      modality: 'suggests',
      speaker_id: 'ent_mara',
      narrates: false,
      perceiver_ids: ['ent_tobin'],
      scene_id: 'pln_docks',
      hearers_believe: true,
      expected_revision: 3,
    })
    const done = await screen.findByRole('dialog', { name: 'Recorded, and beliefs suggested' })
    expect(within(done).getByText(/It was only suggested, so less firmly again/)).toBeInTheDocument()
    expect(within(done).getByText('Believes it, less firmly')).toBeInTheDocument()
    expect(within(done).getByRole('link', { name: 'Review the suggestion' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_2',
    )
  })
})
