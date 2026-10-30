import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { BeliefsPanel } from './BeliefsPanel'

afterEach(() => vi.unstubAllGlobals())

const mara = entity()
const harbor = entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [] })
const everyone = { items: [mara, harbor, tobin], limit: 200, offset: 0 }
const belief = {
  id: 'blf_1',
  holder_entity_id: 'ent_mara',
  subject_id: 'ent_tobin',
  predicate: 'LOCATED_AT',
  object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
  confidence: 0.75,
  source_ref: 'by-hand',
  acquisition_provenance_id: 'prov_1',
  created_revision: 2,
  closed_revision: null,
}

describe('BeliefsPanel', () => {
  it('records what a character believes about someone else, as a sentence', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /beliefs': { items: [] },
      'POST /beliefs': reply(201, belief),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<BeliefsPanel entity={mara} />)

    const panel = await screen.findByRole('region', { name: 'What Mara Venn believes' })
    expect(await within(panel).findByText('Nothing recorded about what Mara Venn believes yet.')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: 'Add belief' }))
    expect(await within(panel).findByRole('alert')).toHaveTextContent('Choose how the sentence continues')

    await user.selectOptions(within(panel).getByLabelText('How sure'), 'is fairly sure')
    await user.selectOptions(within(panel).getByLabelText('Who or what the belief is about'), 'ent_tobin')
    await user.selectOptions(
      within(panel).getByLabelText('How the sentence continues'),
      await within(panel).findByRole('option', { name: /is located at/ }),
    )
    await user.selectOptions(within(panel).getByLabelText('What Tobin Ashe is located at'), 'ent_harbor')
    await user.click(within(panel).getByRole('button', { name: 'Add belief' }))

    await waitFor(() => expect(api.find('POST', '/beliefs')).toHaveLength(1))
    expect(api.find('POST', '/beliefs')[0].body).toEqual({
      holder_entity_id: 'ent_mara',
      proposition: {
        subject_id: 'ent_tobin',
        predicate: 'LOCATED_AT',
        object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
      },
      source_ref: 'by-hand',
      confidence: 0.75,
      expected_revision: 3,
    })
  })

  it('ends a belief and offers to undo it', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /beliefs': { items: [belief] },
      'GET /beliefs/blf_1/truth-agreement': { belief_id: 'blf_1', truth_agreement: 'unknown' },
      'POST /beliefs/blf_1/close': { ...belief, closed_revision: 4 },
      'POST /beliefs': reply(201, { ...belief, id: 'blf_2' }),
    })
    const user = userEvent.setup()
    renderInProject(<BeliefsPanel entity={mara} />)

    const panel = await screen.findByRole('region', { name: 'What Mara Venn believes' })
    expect(await within(panel).findByText('Tobin Ashe is located at Lantern Harbor')).toBeInTheDocument()
    expect(within(panel).getByText('75% sure')).toBeInTheDocument()
    expect(await within(panel).findByText('Your story doesn’t say')).toBeInTheDocument()

    await user.click(
      within(panel).getByRole('button', { name: 'No longer believes: Tobin Ashe is located at Lantern Harbor' }),
    )
    expect(await within(panel).findByText(/Mara Venn no longer believes/)).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.find('POST', '/beliefs')).toHaveLength(1))
    expect(api.find('POST', '/beliefs')[0].body).toMatchObject({ holder_entity_id: 'ent_mara', confidence: 0.75 })
  })

  it('is hidden from a reader when there is nothing to show', async () => {
    mockApi({ 'GET /entities': everyone, 'GET /beliefs': { items: [] } })
    renderInProject(<BeliefsPanel entity={mara} />, { context: { access: VIEWER } })
    await waitFor(() => expect(screen.queryByRole('region')).not.toBeInTheDocument())
  })
})
