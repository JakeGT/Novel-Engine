import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, mutation, operation } from '../test/fixtures'
import {
  expectAccessible,
  mockApi,
  problem,
  renderInProject,
  VIEWER,
  type RecordedCall,
} from '../test/harness'
import { ReviewDetail } from './ReviewDetail'

afterEach(() => vi.unstubAllGlobals())

const at = { path: 'review/:mutationId', at: 'review/mut_1' }
const entries = {
  'GET /entities': { items: [entity()], limit: 200, offset: 0 },
  'GET /assist/sessions': { items: [] },
}

describe('ReviewDetail', () => {
  it('reads each change as a sentence with a before and after', async () => {
    mockApi({ ...entries, 'GET /mutations/mut_1': mutation() })
    const { container } = renderInProject(<ReviewDetail />, at)

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Changes from the AI assistant')
    expect(screen.getByText('Add character “Iven”')).toBeInTheDocument()
    expect(screen.getByText('Update “Mara Venn”')).toBeInTheDocument()
    const diff = screen.getAllByRole('table')[1]
    expect(within(diff).getByRole('row', { name: /Name/ })).toHaveTextContent('Mara VennMara Venn-Marsh')
    expect(screen.getByRole('note')).toHaveTextContent(
      'This needs your approval because it did not come directly from an author',
    )
    expect(screen.getByText('All 2 changes will be added')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('skips one change, then approves and adds the rest to canon in one step', async () => {
    const skipped = mutation({
      operations: [
        operation(),
        operation({
          ordinal: 1,
          payload: { op: 'create_entity', id: 'tmp:b', type: 'Character', name: 'Extra' },
          decision: { decision: 'rejected', actor_user_id: 'usr_1', note: null, operation_revision: 1, created_at: '2026-10-09T10:00:00Z' },
        }),
      ],
    })
    let current = mutation({ operations: [operation(), operation({ ordinal: 1, payload: { op: 'create_entity', id: 'tmp:b', type: 'Character', name: 'Extra' } })] })
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': () => current,
      'POST /operations/1/decision': () => (current = skipped),
      'POST /mutations/mut_1/approve': () => (current = { ...skipped, status: 'approved' }),
      'POST /mutations/mut_1/commit': { mutation_id: 'mut_1', commit_id: 'cmt_9', revision: 4, already_applied: false },
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    const second = await screen.findByRole('group', { name: 'Change 2: Add character “Extra”' })
    await user.click(within(second).getByRole('button', { name: 'Skip' }))
    expect(await screen.findByText('1 of 2 changes will be added')).toBeInTheDocument()
    expect(api.find('POST', '/operations/1/decision')[0].body).toEqual({ decision: 'rejected', note: null })

    await user.click(screen.getByRole('button', { name: 'Approve and add to canon' }))
    expect(await screen.findByText('Added to canon as version 4.')).toBeInTheDocument()
    // Approval is still its own recorded step, sent before the commit.
    expect(api.calls.filter((call) => call.method === 'POST').map((call) => call.path.split('/').pop())).toEqual([
      'decision',
      'approve',
      'commit',
    ])
  })

  it('groups a large set of changes and decides a whole group at once', async () => {
    const extras = ['Ana', 'Bo', 'Cy'].map((name, index) =>
      operation({ ordinal: index, payload: { op: 'create_entity', id: `tmp:${name}`, type: 'Character', name } }),
    )
    let current = mutation({
      operations: [
        ...extras,
        operation({
          ordinal: 3,
          operation_type: 'close_fact',
          payload: { op: 'close_fact', id: 'fct_1' },
          target_before: null,
        }),
      ],
    })
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': () => current,
      'POST /decision': (call: RecordedCall) => {
        const ordinal = Number(call.path.split('/').at(-2))
        current = {
          ...current,
          operations: current.operations.map((item) =>
            item.ordinal === ordinal
              ? { ...item, decision: { decision: 'rejected', actor_user_id: 'usr_1', note: null, operation_revision: 1, created_at: '2026-10-09T10:00:00Z' } }
              : item,
          ),
        }
        return current
      },
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, {
      ...at,
      state: {
        fromAssistant: true,
        understood: 'Three people join the story.',
        notes: ['Bo is mentioned by one other fact.'],
      },
    })

    expect(await screen.findByText('3 new entries · 1 facts ended')).toBeInTheDocument()
    // What the assistant understood, and what else it affects, are shown before deciding.
    expect(screen.getByText(/Three people join the story\./)).toBeInTheDocument()
    expect(screen.getByText(/Bo is mentioned by one other fact\./)).toBeInTheDocument()

    const people = screen.getByRole('region', { name: 'New entries' })
    expect(within(people).getByText('New entries · 3 of 3 kept')).toBeInTheDocument()
    await user.click(within(people).getByRole('button', { name: 'Skip all new entries' }))

    expect(await within(people).findByText('New entries · 0 of 3 kept')).toBeInTheDocument()
    expect(api.find('POST', '/decision')).toHaveLength(3)
    expect(screen.getByText('1 of 4 changes will be added')).toBeInTheDocument()
    // A single-item group has nothing to decide in bulk.
    const ended = screen.getByRole('region', { name: 'Facts ended' })
    expect(within(ended).queryByRole('button', { name: /all/ })).not.toBeInTheDocument()
  })

  it('shows what the assistant was told, in the author’s own words', async () => {
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'GET /assist/sessions': {
        items: [
          {
            id: 'brf_1',
            kind: 'create',
            status: 'proposed',
            actor_user_id: 'usr_1',
            description: 'Iven is Mara’s brother.',
            entity_type: null,
            name: null,
            entity_id: null,
            questions: [{ id: 'q1', question: 'Where does he live?', category: 'place' }],
            answers: [{ question_id: 'q1', question: 'Where does he live?', answer: 'Saltmere.' }],
            understood: 'Add Iven.',
            notes: ['Mara has no family recorded.'],
            mutation_id: 'mut_1',
            created_at: '2026-10-09T10:00:00Z',
            updated_at: '2026-10-09T10:00:00Z',
          },
        ],
      },
    })
    const { container } = renderInProject(<ReviewDetail />, at)

    const evidence = await screen.findByRole('region', { name: 'Where this came from' })
    expect(within(evidence).getByText('Iven is Mara’s brother.')).toBeInTheDocument()
    expect(within(evidence).getByText('Where does he live?')).toBeInTheDocument()
    expect(within(evidence).getByText('Saltmere.')).toBeInTheDocument()
    expect(within(evidence).getByText('Add Iven.')).toBeInTheDocument()
    expect(evidence).toHaveTextContent('Mara has no family recorded.')
    // Asked for by the change it produced, not by an id the author has to know.
    expect(api.find('GET', '/assist/sessions')[0].search.get('mutation_id')).toBe('mut_1')
    await expectAccessible(container)
  })

  it('asks before discarding, says it cannot be brought back, and keeps reviewing by default', async () => {
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/reject': mutation({ status: 'rejected' }),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Discard these changes' }))
    const dialog = screen.getByRole('dialog', { name: 'Discard 2 suggested changes?' })
    expect(dialog).toHaveTextContent('cannot be brought back')
    expect(within(dialog).getByRole('button', { name: 'Keep reviewing' })).toHaveFocus()

    await user.click(within(dialog).getByRole('button', { name: 'Keep reviewing' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.find('POST', '/reject')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Discard these changes' }))
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    await waitFor(() => expect(api.find('POST', '/reject')).toHaveLength(1))
  })

  it('does not claim success when the commit fails because the story moved on', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ status: 'approved' }),
      'POST /mutations/mut_1/commit': problem(409, 'revision_conflict', 'The branch head changed.'),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Add to canon' }))
    expect(await screen.findByText(/Your story changed while you were reviewing/)).toBeInTheDocument()
    expect(screen.queryByText(/Added to canon/)).not.toBeInTheDocument()
  })

  it('blocks adding an out-of-date set of changes until it is updated', async () => {
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ is_stale: true, head_revision: 6 }),
      'POST /mutations/mut_1/retarget': mutation({ expected_revision: 6, head_revision: 6 }),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    expect(await screen.findByText(/Your story has moved on since this was written/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve and add to canon' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Update to version 6' }))
    await waitFor(() => expect(api.find('POST', '/retarget')).toHaveLength(1))
    expect(api.find('POST', '/retarget')[0].body).toEqual({ expected_revision: 6 })
  })

  it('reports problems found by a check against the change they belong to', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/validate': {
        ok: false,
        head_revision: 3,
        issues: [{ ordinal: 0, code: 'slug_conflict', severity: 'blocking', message: 'That short name is taken.' }],
      },
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Check for problems' }))
    expect(await screen.findByText(/problems that would stop these being added/)).toBeInTheDocument()
    const first = screen.getByText('Add character “Iven”').closest('li') as HTMLElement
    expect(within(first).getByRole('alert')).toHaveTextContent('This must be fixed first: That short name is taken.')
  })

  it('keeps an edited change open with the text when the server rejects it', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'PUT /operations/0': problem(422, 'validation_error', 'Unknown entity type.'),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByText('More about change 1'))
    await user.click(screen.getByRole('button', { name: 'Edit change 1' }))
    const editor = screen.getByLabelText('Change 1 (JSON)')
    await user.clear(editor)
    await user.type(editor, '{{"op":"create_entity","id":"tmp:a","type":"Nope","name":"Iven"}')
    await user.click(screen.getByRole('button', { name: 'Save edited change' }))

    expect(await screen.findByText('Unknown entity type.')).toBeInTheDocument()
    expect(screen.getByLabelText('Change 1 (JSON)')).toHaveValue(
      '{"op":"create_entity","id":"tmp:a","type":"Nope","name":"Iven"}',
    )
  })

  it('shows finished and read-only states without any decision controls', async () => {
    mockApi({ ...entries, 'GET /mutations/mut_1': mutation({ status: 'committed', committed_revision: 4 }) })
    const first = renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText('Added to canon as version 4.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /canon/ })).not.toBeInTheDocument()
    first.unmount()

    mockApi({ ...entries, 'GET /mutations/mut_1': mutation() })
    renderInProject(<ReviewDetail />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByText(/You can read this project but not change it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Skip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Discard these changes' })).not.toBeInTheDocument()
  })
})
