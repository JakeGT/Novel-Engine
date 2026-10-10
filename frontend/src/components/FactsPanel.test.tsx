import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, fact, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply, VIEWER, type RecordedCall } from '../test/harness'
import { FactsPanel } from './FactsPanel'

afterEach(() => vi.unstubAllGlobals())

const mara = entity()
const harbor = entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [] })
const everyone = { items: [mara, harbor, tobin], limit: 200, offset: 0 }

function facts(outgoing: unknown[], incoming: unknown[] = []) {
  return (call: RecordedCall) => ({
    items: call.search.get('subject_id') ? outgoing : incoming,
  })
}

describe('FactsPanel', () => {
  it('reads facts as sentences from both sides and never shows a raw id', async () => {
    mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /facts': facts(
        [fact()],
        [fact({ id: 'fct_2', subject_id: 'ent_tobin', predicate: 'KNOWS', object: { kind: 'entity_ref', entity_id: 'ent_mara' } })],
      ),
    })
    const { container } = renderInProject(<FactsPanel entity={mara} />)

    const list = (await screen.findByRole('link', { name: 'Lantern Harbor' })).closest('ul') as HTMLElement
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Mara Venn is located at Lantern Harbor')
    expect(rows[1]).toHaveTextContent('Tobin Ashe knows Mara Venn')
    expect(container).not.toHaveTextContent('ent_')
    await expectAccessible(container)
  })

  it('builds a new fact as a sentence, offering only entries the fact can point to', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /facts': { items: [] },
      'POST /facts': reply(201, fact()),
    })
    const user = userEvent.setup()
    renderInProject(<FactsPanel entity={mara} />)

    await user.click(await screen.findByRole('button', { name: 'Add fact' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose how the sentence continues')

    await user.selectOptions(
      screen.getByLabelText('How the sentence continues'),
      await screen.findByRole('option', { name: /is located at/ }),
    )
    const target = screen.getByLabelText('What Mara Venn is located at')
    // Only locations qualify, and never the entry itself.
    expect(within(target).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Choose…',
      'Lantern Harbor (Location)',
    ])
    await user.selectOptions(target, 'ent_harbor')
    await user.click(screen.getByRole('button', { name: 'Add fact' }))

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
  })

  it('ends a fact, and explains when the change is held for review', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /facts': facts([fact()]),
      'POST /facts/fct_1/close': problem(409, 'review_required', 'Review required.', {
        mutation_id: 'mut_9',
        reasons: 'bulk_change',
      }),
    })
    const user = userEvent.setup()
    renderInProject(<FactsPanel entity={mara} />)

    await user.click(await screen.findByRole('button', { name: 'End fact: is located at Lantern Harbor' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('needs a review before it becomes canon')
    expect(alert).toHaveTextContent('it changes many things at once')
    expect(within(alert).getByRole('link', { name: 'Review the change' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_9',
    )
    expect(api.find('POST', '/facts/fct_1/close')[0].body).toEqual({ expected_revision: 3 })
  })

  it('ends a fact at once and offers to undo it by stating it again', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /facts': facts([fact()]),
      'POST /facts/fct_1/close': fact(),
      'POST /facts': reply(201, fact({ id: 'fct_2' })),
    })
    const user = userEvent.setup()
    renderInProject(<FactsPanel entity={mara} />)

    await user.click(await screen.findByRole('button', { name: 'End fact: is located at Lantern Harbor' }))
    const notice = await screen.findByText(/No longer true: “Mara Venn is located at Lantern Harbor”/)
    await user.click(within(notice.closest('[role="status"]') as HTMLElement).getByRole('button', { name: 'Undo' }))

    await waitFor(() => expect(api.find('POST', '/facts').filter((call) => !call.path.includes('/close'))).toHaveLength(1))
    const restated = api.find('POST', '/facts').filter((call) => !call.path.includes('/close'))[0]
    expect(restated.body).toMatchObject({
      proposition: { subject_id: 'ent_mara', predicate: fact().predicate, object: fact().object },
    })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument())
  })

  it('offers no editing to a read-only member', async () => {
    mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities': everyone,
      'GET /facts': facts([fact()]),
    })
    renderInProject(<FactsPanel entity={mara} />, { context: { access: VIEWER } })

    await screen.findByRole('link', { name: 'Lantern Harbor' })
    expect(screen.queryByRole('button', { name: /End fact/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Add a fact' })).not.toBeInTheDocument()
  })
})
