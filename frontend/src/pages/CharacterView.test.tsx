import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Belief, CanonEvent, CharacterProposal, CharacterQuestionSheet } from '../api/client'
import { entity, fact, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply } from '../test/harness'
import { CharacterView } from './CharacterView'

afterEach(() => vi.unstubAllGlobals())

const belief: Belief = {
  id: 'blf_1',
  holder_entity_id: 'ent_mara',
  subject_id: 'ent_keep',
  predicate: 'IS_HAUNTED',
  object: { kind: 'boolean', value: true },
  confidence: 0.8,
  source_ref: 'scene:ch01',
  acquisition_provenance_id: 'prov_1',
  created_revision: 2,
  closed_revision: null,
}

const event: CanonEvent = {
  id: 'evt_1',
  kind: 'shipwreck',
  name: 'Wreck of the Dawn Runner',
  participants: [{ entity_id: 'ent_mara', role: 'witness' }],
  location_ids: [],
  cause_ids: [],
  outcome_ids: [],
  world_position: null,
  provenance_id: 'prov_1',
  created_revision: 2,
}

const sheet: CharacterQuestionSheet = {
  entity_id: 'ent_mara',
  questions: [
    { id: 'where_1', question: 'Where does she live?', category: 'place' },
    { id: 'fear_1', question: 'What does she fear?', category: 'personality' },
  ],
}

const proposal: CharacterProposal = {
  mutation_id: 'mut_7',
  status: 'needs_review',
  operation_count: 2,
  session_id: 'qnr_1',
  discarded: [{ kind: 'fact', reason: "unsupported predicate 'ADORES'" }],
  validation_ok: true,
  validation_issues: [],
}

const page = { path: 'characters/:entityId', at: 'characters/ent_mara' }

const routes = {
  'GET /entities/ent_mara': entity(),
  'GET /entities': {
    items: [entity(), entity({ id: 'ent_keep', name: 'Saltkeep', type: 'Location', aliases: [] })],
    limit: 200,
    offset: 0,
  },
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /facts': { items: [fact()], limit: 200, offset: 0 },
  'GET /beliefs': { items: [belief], limit: 200, offset: 0 },
  'GET /beliefs/blf_1/truth-agreement': { belief_id: 'blf_1', truth_agreement: 'disagrees' },
  'GET /events': { items: [event], limit: 200, offset: 0 },
  'GET /links': { items: [] },
  'GET /provenance/entity/ent_mara': { items: [] },
}

describe('CharacterView', () => {
  it('shows beliefs with a derived comparison to canon, and events with unknown dates', async () => {
    const api = mockApi(routes)
    const { container } = renderInProject(<CharacterView />, page)

    expect(await screen.findByText(/Saltkeep IS_HAUNTED → true/)).toBeInTheDocument()
    expect(await screen.findByText('Compared with canon: Contradicts canon')).toBeInTheDocument()
    expect(screen.getByText(/it is never stored with the belief/)).toBeInTheDocument()
    expect(screen.getByText('Wreck of the Dawn Runner')).toBeInTheDocument()
    expect(screen.getByText(/date unknown/)).toBeInTheDocument()
    // Events are requested for this character, not filtered from a page of all events.
    expect(api.find('GET', '/events')[0].search.get('entity_id')).toBe('ent_mara')
    await expectAccessible(container)
  })

  it('explains a missing AI provider without implying anything changed', async () => {
    mockApi({
      ...routes,
      'POST /characters/ent_mara/questions': problem(
        503,
        'capability_unavailable',
        'No provider credential is configured for this workspace.',
      ),
    })
    const user = userEvent.setup()
    renderInProject(<CharacterView />, page)

    await user.click(await screen.findByRole('button', { name: 'Generate questions' }))

    const alert = await screen.findByText(/needs an AI provider/)
    expect(alert).toHaveTextContent('Nothing was changed')
    expect(alert).toHaveTextContent('by hand')
  })

  it('turns answers into a proposal and sends the author to review it', async () => {
    const api = mockApi({
      ...routes,
      'POST /characters/ent_mara/questions': sheet,
      'POST /characters/ent_mara/proposals': reply(201, proposal),
    })
    const user = userEvent.setup()
    const { context } = renderInProject(<CharacterView />, page)

    await user.click(await screen.findByRole('button', { name: 'Generate questions' }))
    await user.type(await screen.findByLabelText(/Where does she live\?/), 'By the harbor.')
    await user.click(screen.getByRole('button', { name: 'Create proposal for review' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent(
        '/projects/prj_1/mutations/mut_7',
      ),
    )
    // Only answered questions are sent, against the freshly read head.
    expect(context.refreshHead).toHaveBeenCalled()
    expect(api.find('POST', '/proposals')[0].body).toEqual({
      answers: [{ question_id: 'where_1', question: 'Where does she live?', answer: 'By the harbor.' }],
      expected_revision: 5,
    })
  })
})
