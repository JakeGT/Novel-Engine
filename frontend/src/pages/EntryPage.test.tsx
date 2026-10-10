import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, VIEWER } from '../test/harness'
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
  'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
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
    const api = mockApi({ ...base, 'PATCH /entities/ent_mara': entity({ name: 'Mara Marsh' }) })
    const user = userEvent.setup()
    renderInProject(<EntryPage />, at)

    await user.click((await screen.findAllByRole('button', { name: 'Edit' }))[0])
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
    expect(await within(beliefs).findByText('Contradicts canon')).toBeInTheDocument()
  })

  it('turns interview answers into suggestions for review, never straight into canon', async () => {
    const api = mockApi({
      ...base,
      'POST /characters/ent_mara/questions': {
        session_id: 's1',
        entity_id: 'ent_mara',
        questions: [
          { id: 'q1', question: 'Where did Mara grow up?', category: 'history' },
          { id: 'q2', question: 'Who does she trust?', category: 'relationships' },
        ],
      },
      'POST /characters/ent_mara/proposals': {
        session_id: 's1',
        mutation_id: 'mut_5',
        status: 'needs_review',
        operation_count: 1,
        discarded: [],
        validation_ok: true,
        validation_issues: [],
      },
    })
    const user = userEvent.setup()
    renderInProject(<EntryPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Start the interview' }))
    await user.type(await screen.findByLabelText('Where did Mara grow up?'), 'Saltmere')
    await user.click(screen.getByRole('button', { name: 'Turn answers into suggestions' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_5'),
    )
    // Only answered questions are sent, against the freshly read head.
    expect(api.find('POST', '/proposals')[0].body).toEqual({
      answers: [{ question_id: 'q1', question: 'Where did Mara grow up?', answer: 'Saltmere' }],
      expected_revision: 5,
    })
  })

  it('explains a missing AI provider and points to where a key is connected', async () => {
    mockApi({
      ...base,
      'POST /characters/ent_mara/questions': problem(503, 'capability_unavailable', 'No provider.'),
    })
    const user = userEvent.setup()
    renderInProject(<EntryPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Start the interview' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('none is connected right now')
    expect(alert).toHaveTextContent('Nothing was changed')
    expect(within(alert).getByRole('link', { name: 'Connect an AI key' })).toHaveAttribute(
      'href',
      '/projects/prj_1/assistant',
    )
  })

  it('is read-only for a viewer and for an archived entry', async () => {
    mockApi(base)
    const first = renderInProject(<EntryPage />, { ...at, context: { access: VIEWER } })
    await screen.findByRole('heading', { level: 1, name: 'Mara Venn' })
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start the interview' })).not.toBeInTheDocument()
    first.unmount()

    mockApi({ ...base, 'GET /entities/ent_mara': entity({ lifecycle: 'retired', retired_revision: 4 }) })
    renderInProject(<EntryPage />, at)
    expect(await screen.findByText(/This entry is archived/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })
})
