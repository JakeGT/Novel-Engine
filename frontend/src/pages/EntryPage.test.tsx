import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EMPTY_TIMELINE, entity, knowledge, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { EntryPage } from './EntryPage'

afterEach(() => vi.unstubAllGlobals())

const mara = entity()
const harbor = entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const base = {
  'GET /entities/ent_mara': mara,
  'GET /entities': { items: [mara, harbor], limit: 200, offset: 0 },
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /facts': { items: [] },
  'GET /beliefs': { items: [] },
  'GET /events': { items: [] },
  'GET /links': { items: [] },
  'GET /provenance/entity/ent_mara': { items: [] },
  'GET /knowledge/characters/ent_mara': knowledge('ent_mara'),
  'GET /knowledge/characters/ent_mara/trust': { entity_id: 'ent_mara', default: 'believes', speakers: {} },
  'GET /knowledge/claims': { items: [] },
  'GET /timeline': EMPTY_TIMELINE,
  'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
  'GET /plan': { items: [], open: [], conditions: [] },
}
const at = { path: 'entries/:entityId', at: 'entries/ent_mara' }

const belief = {
  id: 'blf_1',
  holder_entity_id: 'ent_mara',
  subject_id: 'ent_mara',
  predicate: 'LOCATED_AT',
  object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
  confidence: 0.6,
  source_ref: null,
  acquisition_provenance_id: 'prov_1',
  created_revision: 2,
  closed_revision: null,
}

describe('EntryPage', () => {
  it('shows an entry in plain words with details, not raw data', async () => {
    mockApi(base)
    const { container } = renderInProject(<EntryPage />, at)

    expect(await screen.findByRole('heading', { level: 1, name: 'Mara Venn' })).toBeInTheDocument()
    expect(screen.getByText('also known as The Fox')).toBeInTheDocument()
    const details = screen.getByRole('region', { name: 'Details' })
    expect(within(details).getByText('Role')).toBeInTheDocument()
    expect(within(details).getByText('pilot')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Characters/ })).toHaveAttribute(
      'href',
      '/projects/prj_1/bible?type=Character',
    )
    await expectAccessible(container)
  })

  it('edits in a dialog and closes it once saved', async () => {
    const api = mockApi({
      ...base,
      'PATCH /entities/ent_mara': entity({ name: 'Mara Marsh' }),
      'GET /entities/ent_mara/impact': {
        about: ['Mara Venn is changed'],
        affected: [],
        may_be_affected: [],
        not_checked: ['Checks and notes in the scene studio.'],
      },
    })
    const user = userEvent.setup()
    renderInProject(<EntryPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Edit entry' }))
    // What depends on the entry is offered before saving, without taking the form's place.
    await user.click(await screen.findByText(/What changing Mara Venn touches/))
    expect(await screen.findByText(/What changing Mara Venn touches/)).toHaveTextContent('nothing found')
    const form = screen.getByRole('form', { name: 'Edit Mara Venn' })
    const name = within(form).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Mara Marsh')
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.find('PATCH', '/entities/ent_mara')[0].body).toEqual({ name: 'Mara Marsh', expected_revision: 3 })
  })

  it('compares what a character believes with canon each time it is shown', async () => {
    mockApi({
      ...base,
      'GET /beliefs': { items: [belief] },
      'GET /beliefs/blf_1/truth-agreement': { belief_id: 'blf_1', truth_agreement: 'disagrees' },
    })
    renderInProject(<EntryPage />, at)

    const beliefs = await screen.findByRole('region', { name: 'What Mara Venn believes' })
    expect(within(beliefs).getByText('Mara Venn is located at Lantern Harbor')).toBeInTheDocument()
    expect(within(beliefs).getByText('60% sure')).toBeInTheDocument()
    expect(await within(beliefs).findByText('Not so in your story')).toBeInTheDocument()
  })

  it('offers the assistant on a bare entry first, and more quietly once it has details', async () => {
    const fillIn = vi.fn()
    const bareEntity = entity({ attributes: {} })
    mockApi({ ...base, 'GET /entities/ent_mara': bareEntity })
    const user = userEvent.setup()
    const first = renderInProject(<EntryPage />, { ...at, context: { fillIn } })

    expect(await screen.findByRole('heading', { name: 'Mara Venn is just a name so far' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Fill it in with the assistant' }))
    expect(fillIn).toHaveBeenCalledWith(bareEntity)
    first.unmount()

    mockApi(base)
    renderInProject(<EntryPage />, { ...at, context: { fillIn } })
    expect(await screen.findByRole('button', { name: 'Ask me about it' })).toBeInTheDocument()
    expect(screen.queryByText(/just a name so far/)).not.toBeInTheDocument()
  })

  it('is read-only for a viewer and for an archived entry', async () => {
    mockApi(base)
    const first = renderInProject(<EntryPage />, { ...at, context: { access: VIEWER } })
    await screen.findByRole('heading', { level: 1, name: 'Mara Venn' })
    expect(screen.queryByRole('button', { name: 'Edit entry' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archive this entry' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /assistant|Ask me/ })).not.toBeInTheDocument()
    first.unmount()

    mockApi({ ...base, 'GET /entities/ent_mara': entity({ lifecycle: 'retired', retired_revision: 4 }) })
    renderInProject(<EntryPage />, at)
    expect(await screen.findByText(/This entry is archived/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit entry' })).not.toBeInTheDocument()
  })
})
