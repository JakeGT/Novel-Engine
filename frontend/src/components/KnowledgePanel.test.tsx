import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, fact } from '../test/fixtures'
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

  it('is not shown for anything but a character', () => {
    mockApi({})
    renderInProject(<KnowledgePanel entity={harbor} />)
    expect(screen.queryByRole('region', { name: /knows/ })).not.toBeInTheDocument()
  })
})
