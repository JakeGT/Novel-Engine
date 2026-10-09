import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, fact, VOCABULARY } from '../test/fixtures'
import {
  expectAccessible,
  mockApi,
  renderInProject,
  reply,
  VIEWER,
  type RecordedCall,
} from '../test/harness'
import { RelationshipsPanel } from './RelationshipsPanel'

afterEach(() => vi.unstubAllGlobals())

const harbor = entity({ id: 'ent_harbor', name: 'Lantern Harbor', type: 'Location', aliases: [] })
const iven = entity({ id: 'ent_iven', name: 'Iven', aliases: [] })
const routes = {
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /entities': { items: [entity(), harbor, iven], limit: 200, offset: 0 },
  // Answers like the server: each side of a relationship is its own filtered query.
  'GET /facts': (call: RecordedCall) => {
    const all = [
      fact(),
      fact({
        id: 'fct_2',
        subject_id: 'ent_iven',
        predicate: 'KNOWS',
        object: { kind: 'entity_ref', entity_id: 'ent_mara', project_id: 'prj_1' },
      }),
      fact({ id: 'fct_3', subject_id: 'ent_iven', predicate: 'HAS_NAME', object: { kind: 'string', value: 'Iven' } }),
    ]
    const subject = call.search.get('subject_id')
    const object = call.search.get('object_entity_id')
    const items = all.filter(
      (item) =>
        (!subject || item.subject_id === subject) &&
        (!object || (item.object.kind === 'entity_ref' && item.object.entity_id === object)),
    )
    return { items, limit: 200, offset: 0 }
  },
}

describe('RelationshipsPanel', () => {
  it('shows facts in both directions and asserts a typed fact from the vocabulary', async () => {
    const api = mockApi({
      ...routes,
      'POST /facts': reply(201, fact({ id: 'fct_new' })),
      'POST /facts/fct_1/close': fact({ status: 'closed', closed_revision: 4 }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<RelationshipsPanel entity={entity()} />)

    // Outgoing and incoming facts, with names resolved; unrelated facts are not shown.
    expect(await screen.findByText(/→ Lantern Harbor/)).toBeInTheDocument()
    // Each direction is filtered by the server, never by scanning a page of all facts.
    const filters = api.find('GET', '/facts').map((call) => call.search.toString())
    expect(filters).toContain('subject_id=ent_mara&limit=200')
    expect(filters).toContain('object_entity_id=ent_mara&limit=200')
    expect(screen.getByText(/Iven/, { selector: 'li' })).toHaveTextContent('Iven KNOWS → this')
    expect(screen.queryByText(/HAS_NAME →/)).not.toBeInTheDocument()

    // Only predicates valid for a Character are offered; targets are limited by type.
    const predicate = screen.getByLabelText('Predicate')
    await user.selectOptions(predicate, 'LOCATED_AT')
    const value = screen.getByLabelText('Value')
    expect(value).toContainHTML('Lantern Harbor (Location)')
    expect(value).not.toContainHTML('Iven (Character)')
    await expectAccessible(container)
    await user.selectOptions(value, 'ent_harbor')
    await user.click(screen.getByRole('button', { name: 'Assert fact' }))

    await waitFor(() => expect(api.find('POST', '/facts')).toHaveLength(1))
    expect(api.find('POST', '/facts')[0].body).toEqual({
      proposition: {
        subject_id: 'ent_mara',
        predicate: 'LOCATED_AT',
        object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
      },
      confidence: 1,
      expected_revision: 3,
    })

    await user.click(screen.getByRole('button', { name: 'Close fact LOCATED_AT Lantern Harbor' }))
    await waitFor(() =>
      expect(api.find('POST', '/facts/fct_1/close')[0].body).toEqual({ expected_revision: 3 }),
    )
  })

  it('is read-only for a viewer', async () => {
    mockApi(routes)
    renderInProject(<RelationshipsPanel entity={entity()} />, { context: { access: VIEWER } })

    expect(await screen.findByText(/→ Lantern Harbor/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Close fact/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Assert fact' })).not.toBeInTheDocument()
  })
})
