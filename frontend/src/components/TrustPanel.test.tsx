import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { VIEWER, expectAccessible, mockApi, renderInProject } from '../test/harness'
import { TrustPanel } from './TrustPanel'

afterEach(() => vi.unstubAllGlobals())

const mara = entity()
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [], attributes: {} })
const ines = entity({ id: 'ent_ines', name: 'Ines', aliases: [], attributes: {} })
const harbor = entity({ id: 'ent_harbor', name: 'Lantern Harbor', type: 'Location', aliases: [] })

describe('TrustPanel', () => {
  it('says how a character takes what others say, in general and from particular people', async () => {
    const api = mockApi({
      'GET /entities': { items: [mara, tobin, ines, harbor], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin/trust': {
        entity_id: 'ent_tobin',
        default: 'believes',
        speakers: { ent_mara: 'disbelieves' },
      },
      'PUT /knowledge/characters/ent_tobin/trust': (call: { body: unknown }) => ({
        entity_id: 'ent_tobin',
        ...(call.body as object),
      }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<TrustPanel entity={tobin} />)

    const panel = await screen.findByRole('region', { name: 'How Tobin Ashe takes what others say' })
    expect(await within(panel).findByText('Mara Venn')).toBeInTheDocument()
    expect(within(panel).getByRole('combobox', { name: 'What Tobin Ashe makes of what Mara Venn says' })).toHaveValue(
      'disbelieves',
    )
    // Only other characters can be chosen; nothing has changed, so nothing to save.
    const someone = within(panel).getByRole('combobox', { name: 'Someone in particular' })
    expect(within(someone).queryByText('Lantern Harbor')).not.toBeInTheDocument()
    expect(within(someone).queryByText('Mara Venn')).not.toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Save' })).toBeDisabled()
    await expectAccessible(container)

    await user.selectOptions(
      within(panel).getByRole('combobox', { name: /When anyone tells Tobin Ashe something/ }),
      'doubts',
    )
    await user.selectOptions(someone, 'ent_ines')
    await user.selectOptions(within(panel).getByRole('combobox', { name: 'What they make of it' }), 'believes')
    await user.click(within(panel).getByRole('button', { name: 'Add' }))
    await user.click(within(panel).getByRole('button', { name: 'Take Mara Venn like anyone else' }))
    await user.click(within(panel).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.find('PUT', '/trust')).toHaveLength(1))
    expect(api.find('PUT', '/trust')[0].body).toEqual({
      default: 'doubts',
      speakers: { ent_ines: 'believes' },
      expected_revision: 3,
    })
  })

  it('shows trust without changing it to someone who can only read', async () => {
    mockApi({
      'GET /entities': { items: [mara, tobin], limit: 200, offset: 0 },
      'GET /knowledge/characters/ent_tobin/trust': {
        entity_id: 'ent_tobin',
        default: 'doubts',
        speakers: { ent_mara: 'believes' },
      },
    })
    renderInProject(<TrustPanel entity={tobin} />, { context: { access: VIEWER } })
    const panel = await screen.findByRole('region', { name: 'How Tobin Ashe takes what others say' })
    expect(await within(panel).findByText('Believes it')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('is not shown for anything but a character', () => {
    mockApi({})
    renderInProject(<TrustPanel entity={harbor} />)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})
