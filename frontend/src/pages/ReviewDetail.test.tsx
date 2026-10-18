import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, mutation, operation, VOCABULARY } from '../test/fixtures'
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
    // One notice says where things stand, then the changes follow directly.
    const standing = screen.getAllByRole('status')[0]
    expect(standing).toHaveTextContent('Not part of your story yet.')
    expect(standing).toHaveTextContent('It needs your approval because it did not come directly from an author')
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

    await user.click(screen.getByRole('button', { name: 'Approve and add to your story' }))
    // The result is announced where the author is looking, with the next step in it.
    const added = await screen.findByRole('dialog', { name: 'Added to your story' })
    expect(added).toHaveTextContent('The change is now part of your story, saved as version 4.')
    expect(within(added).getByRole('link', { name: 'See what to fill in next' })).toHaveAttribute('href', '/projects/prj_1')
    await user.click(within(added).getByRole('button', { name: 'Stay on this page' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('Added to your story as version 4.')).toBeInTheDocument()
    // Approval is still its own recorded step, sent before the commit.
    const writes = api.calls
      .filter((call) => call.method === 'POST' && !call.path.endsWith('/validate'))
      .map((call) => call.path.split('/').pop())
    expect(writes).toEqual([
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
      'POST /operations/decisions': (call: RecordedCall) => {
        const chosen = (call.body as { decisions: { ordinal: number }[] }).decisions.map((item) => item.ordinal)
        current = {
          ...current,
          operations: current.operations.map((item) =>
            chosen.includes(item.ordinal)
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

    expect(await screen.findByText('3 new entries · 1 fact ended')).toBeInTheDocument()
    // What the assistant understood, and what else it affects, are shown before deciding.
    expect(screen.getByText(/Three people join the story\./)).toBeInTheDocument()
    expect(screen.getByText(/Bo is mentioned by one other fact\./)).toBeInTheDocument()

    const people = screen.getByRole('region', { name: 'New entries' })
    expect(within(people).getByText('New entries · 3 of 3 kept')).toBeInTheDocument()
    await user.click(within(people).getByRole('button', { name: 'Skip all new entries' }))

    expect(await within(people).findByText('New entries · 0 of 3 kept')).toBeInTheDocument()
    // One request carries the whole group, so it is recorded together or not at all.
    expect(api.find('POST', '/operations/decisions')).toHaveLength(1)
    expect(api.find('POST', '/operations/decisions')[0].body).toEqual({
      decisions: [0, 1, 2].map((ordinal) => ({ ordinal, decision: 'rejected' })),
    })
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
            suggestions: [],
            mutation_id: 'mut_1',
            created_at: '2026-10-09T10:00:00Z',
            updated_at: '2026-10-09T10:00:00Z',
          },
        ],
      },
    })
    const { container } = renderInProject(<ReviewDetail />, at)

    // What the assistant understood and noticed is needed to decide, so it is in view.
    const standing = (await screen.findByText(/The assistant understood: Add Iven\./)).closest(
      '[role="status"]',
    ) as HTMLElement
    expect(standing).toHaveTextContent('Mara has no family recorded.')
    // The author's own words are there to check against, closed until asked for,
    // under a label that says what is inside. They come after the changes.
    const summary = screen.getByText('What you told the assistant: your description and one answer')
    const evidence = summary.closest('details') as HTMLDetailsElement
    expect(evidence.open).toBe(false)
    const changes = screen.getByRole('heading', { name: 'The changes' })
    expect(changes.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await userEvent.setup().click(summary)
    expect(within(evidence).getByText('Iven is Mara’s brother.')).toBeVisible()
    expect(within(evidence).getByText('Where does he live?')).toBeInTheDocument()
    expect(within(evidence).getByText('Saltmere.')).toBeInTheDocument()
    // Asked for by the change it produced, not by an id the author has to know.
    expect(api.find('GET', '/assist/sessions')[0].search.get('mutation_id')).toBe('mut_1')
    await expectAccessible(container)
  })

  it('offers what the assistant found missing from the setup, adds it, and drafts again', async () => {
    let added = false
    const session = {
      id: 'brf_1',
      kind: 'fill_in',
      status: 'proposed',
      actor_user_id: 'usr_1',
      description: 'He experiences the world through sound.',
      entity_type: 'Character',
      name: 'John Williams',
      entity_id: 'ent_mara',
      questions: [],
      answers: [],
      understood: '',
      notes: [],
      suggestions: [
        {
          kind: 'fact_kind',
          name: 'EXPERIENCES_WORLD_THROUGH',
          description: '',
          reason: 'He experiences the world through sound.',
          subject_types: ['Character'],
          points_to: 'text',
          object_types: [],
          one_at_a_time: false,
        },
      ],
      mutation_id: 'mut_1',
      created_at: '2026-10-09T10:00:00Z',
      updated_at: '2026-10-09T10:00:00Z',
    }
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'GET /assist/sessions': { items: [session] },
      'GET /schemas/vocabulary': () => ({
        schema_version_id: 'scv_1',
        schema_version: 1,
        entity_types: [{ name: 'Character', core: true, attributes_schema: null }],
        predicates: added
          ? [{ predicate: 'EXPERIENCES_WORLD_THROUGH', core: false, subject_types: ['Character'], object_shape: 'string', object_types: [], cardinality: 'many', temporal_allowed: false }]
          : [],
      }),
      'POST /assist/sessions/brf_1/suggestions/apply': () => {
        added = true
        return { added: ['EXPERIENCES_WORLD_THROUGH'], schema_version: 2 }
      },
      'POST /assist/proposals': {
        mutation_id: 'mut_2',
        status: 'needs_review',
        operation_count: 2,
        session_id: 'brf_2',
        discarded: [],
        suggestions: [],
        validation_ok: true,
        validation_issues: [],
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<ReviewDetail />, at)

    const section = await screen.findByRole('region', { name: 'Suggested additions to your setup' })
    expect(section).toHaveTextContent('A kind of fact: “experiences world through”')
    expect(section).toHaveTextContent('Connects a Character to a piece of text.')
    expect(section).toHaveTextContent('Why: He experiences the world through sound.')
    // The stored name is not shown, and nothing is offered to redraft with yet.
    expect(section).not.toHaveTextContent('EXPERIENCES_WORLD_THROUGH')
    expect(within(section).queryByRole('button', { name: /Draft again/ })).not.toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(section).getByRole('button', { name: 'Add this to my setup' }))
    await waitFor(() => expect(api.find('POST', '/suggestions/apply')).toHaveLength(1))
    expect(api.find('POST', '/suggestions/apply')[0].body).toEqual({ names: ['EXPERIENCES_WORLD_THROUGH'] })
    expect(await within(section).findByText('In your setup')).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: /to my setup/ })).not.toBeInTheDocument()

    await user.click(within(section).getByRole('button', { name: 'Draft again using them' }))
    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_2'),
    )
    // The same words, as a new conversation: the first one stays as it was.
    expect(api.find('POST', '/assist/proposals')[0].body).toEqual({
      description: 'He experiences the world through sound.',
      entity_type: null,
      name: null,
      entity_id: 'ent_mara',
      session_id: null,
      answers: [],
      expected_revision: 5,
    })
  })

  it('shows changed details one per row, not as one run-on line', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({
        operations: [
          operation({
            ordinal: 0,
            operation_type: 'update_entity',
            payload: { op: 'update_entity', id: 'ent_mara', attributes: { role: 'composer', eyes: 'blue', build: 'average' } },
            target_before: { name: 'Mara Venn', attributes: { eyes: 'blue', role: 'pilot' } },
          }),
        ],
      }),
    })
    renderInProject(<ReviewDetail />, at)

    const table = await screen.findByRole('table')
    expect(within(table).getByRole('row', { name: /Role/ })).toHaveTextContent('Rolepilotcomposer')
    expect(within(table).getByRole('row', { name: /Build/ })).toHaveTextContent('Build—average')
    // What did not change is not listed as a change.
    expect(within(table).queryByRole('row', { name: /Eyes/ })).not.toBeInTheDocument()
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

    await user.click(await screen.findByRole('button', { name: 'Add to your story' }))
    expect(await screen.findByText(/Your story changed while you were reviewing/)).toBeInTheDocument()
    expect(screen.queryByText(/Added to your story/)).not.toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'Approve and add to your story' })).toBeDisabled()
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
        issues: [
          { ordinal: 0, code: 'slug_conflict', severity: 'blocking', message: 'That short name is taken.' },
          { ordinal: 1, code: 'possible_duplicate', severity: 'advisory', message: 'You already have a location called “Mara Venn-Marsh”.' },
        ],
      },
    })
    renderInProject(<ReviewDetail />, at)

    // Checked on opening: nobody has to ask.
    expect(await screen.findByText(/problems that would stop these being added/)).toBeInTheDocument()
    const first = screen.getByText('Add character “Iven”').closest('li') as HTMLElement
    expect(within(first).getByRole('alert')).toHaveTextContent('This must be fixed first: That short name is taken.')
    const second = screen.getByText('Update “Mara Venn”').closest('li') as HTMLElement
    expect(within(second).getByRole('note')).toHaveTextContent('Worth a look: You already have a location')
  })

  it('holds back a set that breaks a story rule set to block, and says how to go on', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/validate': {
        ok: false,
        head_revision: 3,
        issues: [
          {
            ordinal: 0,
            code: 'rule:inv_1',
            severity: 'blocking',
            message: 'This breaks your rule “Guilds have members”, which you set to block: it would no longer hold for Harbor Guild.',
          },
        ],
      },
    })
    const { container } = renderInProject(<ReviewDetail />, at)
    const first = (await screen.findByText('Add character “Iven”')).closest('li') as HTMLElement
    expect(await within(first).findByRole('alert')).toHaveTextContent('This must be fixed first: This breaks your rule')
    expect(screen.getByRole('button', { name: 'Approve and add to your story' })).toBeDisabled()
    expect(screen.getByText(/A change breaks a story rule you set to block/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'set the rule to warn' })).toHaveAttribute('href', '/projects/prj_1/rules')
    await expectAccessible(container)
  })

  it('undoes a set of changes that was just added, in one step from where it was added', async () => {
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ status: 'committed', committed_revision: 3 }),
      'POST /reverts/preview': {
        from_revision: 2,
        to_revision: 3,
        head_revision: 3,
        operations: [{ op: 'retire_entity', id: 'ent_mara' }],
        changes: [],
        conflicts: [],
        summary: 'revert r3',
      },
      'POST /reverts': { mutation_id: 'mut_9', commit_id: 'cmt_9', revision: 4, already_applied: false },
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Undo these changes' }))
    const dialog = screen.getByRole('dialog', { name: 'Undo these changes?' })
    expect(await within(dialog).findByText('Archive “Mara Venn”')).toBeInTheDocument()
    // Going back to the version just before this one takes back exactly these changes.
    expect(api.find('POST', '/reverts/preview')[0].body).toEqual({ from_revision: 2, to_revision: null })
    expect(within(dialog).getByRole('button', { name: 'Keep the changes' })).toHaveFocus()

    await user.click(within(dialog).getByLabelText(/I understand this changes a lot/))
    await user.click(within(dialog).getByRole('button', { name: 'Undo these changes' }))

    expect(await screen.findByText(/Undone\. Your story is back as it was, saved as version 4\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Undo these changes' })).not.toBeInTheDocument()
  })

  it('does not offer a one-step undo once other changes have been saved after it', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ status: 'committed', committed_revision: 2 }),
    })
    renderInProject(<ReviewDetail />, at)

    // Undoing it alone would also undo the later work, so the author is sent to History instead.
    expect(await screen.findByText(/can no longer be undone on its own/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Undo these changes' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Go back to an earlier version in History/ })).toHaveAttribute(
      'href',
      '/projects/prj_1/history',
    )
  })

  it('edits a suggested entry with a form, sending only the fields the author changed', async () => {
    const api = mockApi({
      ...entries,
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /mutations/mut_1': mutation(),
      'PUT /operations/0': mutation(),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Edit change 1: Add character “Iven”' }))
    const form = screen.getByRole('form', { name: 'Edit change 1' })
    const name = within(form).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Iven Roe')
    await user.type(within(form).getByLabelText('Tags'), 'rival')
    await user.click(within(form).getByRole('button', { name: 'Add a detail' }))
    await user.type(within(form).getByLabelText('Detail 1 name'), 'role')
    await user.type(within(form).getByLabelText('Detail 1 value'), 'smuggler')
    await user.click(within(form).getByRole('button', { name: 'Save edit' }))

    await waitFor(() => expect(api.find('PUT', '/operations/0')).toHaveLength(1))
    expect(api.find('PUT', '/operations/0')[0].body).toMatchObject({
      operation: {
        op: 'create_entity',
        id: 'tmp:a',
        name: 'Iven Roe',
        attributes: { role: 'smuggler', _tags: ['rival'] },
      },
    })
  })

  it('edits a suggested fact as a sentence, and keeps the form open when it is unfinished', async () => {
    const fact = operation({
      operation_type: 'assert_fact',
      payload: {
        op: 'assert_fact',
        id: 'tmp:fact-0',
        proposition: {
          subject_id: 'ent_mara',
          predicate: 'LOCATED_AT',
          object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
        },
      },
    })
    const api = mockApi({
      'GET /assist/sessions': { items: [] },
      'GET /entities': {
        items: [
          entity(),
          entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor' }),
          entity({ id: 'ent_cove', type: 'Location', name: 'Gull Cove' }),
        ],
        limit: 200,
        offset: 0,
      },
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /mutations/mut_1': mutation({ operations: [fact] }),
      'PUT /operations/0': mutation({ operations: [fact] }),
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: /^Edit change 1:/ }))
    const form = await screen.findByRole('form', { name: 'Edit change 1' })
    const target = within(form).getByLabelText('Who or what')
    expect(target).toHaveValue('ent_harbor')

    await user.selectOptions(target, '')
    await user.click(within(form).getByRole('button', { name: 'Save edit' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('Finish the sentence first.')
    expect(api.find('PUT', '/operations/0')).toHaveLength(0)

    await user.selectOptions(target, 'ent_cove')
    await user.click(within(form).getByRole('button', { name: 'Save edit' }))
    await waitFor(() => expect(api.find('PUT', '/operations/0')).toHaveLength(1))
    expect(api.find('PUT', '/operations/0')[0].body).toMatchObject({
      operation: {
        op: 'assert_fact',
        proposition: {
          subject_id: 'ent_mara',
          predicate: 'LOCATED_AT',
          object: { kind: 'entity_ref', entity_id: 'ent_cove', project_id: 'prj_1' },
        },
      },
    })
  })

  it('revises the changes by asking, and keeps the request when the assistant needs more', async () => {
    let answer: Record<string, unknown> = {
      mutation_id: 'mut_1',
      understood: 'Unclear.',
      edited: 0,
      skipped: 0,
      questions: ['Which Iven do you mean?'],
      notes: [],
      discarded: [],
      validation_ok: true,
    }
    const api = mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'POST /assist/revisions': () => answer,
    })
    const user = userEvent.setup()
    renderInProject(<ReviewDetail />, at)

    await user.click(await screen.findByRole('button', { name: 'Ask the assistant to change these' }))
    const form = screen.getByRole('form', { name: 'Ask the assistant to change these' })
    const box = within(form).getByLabelText('What should be different?')
    await user.type(box, 'Iven is older')
    await user.click(within(form).getByRole('button', { name: 'Ask' }))

    expect(await within(form).findByText('Which Iven do you mean?')).toBeInTheDocument()
    expect(box).toHaveValue('Iven is older')
    expect(api.find('POST', '/assist/revisions')[0].body).toEqual({ mutation_id: 'mut_1', instruction: 'Iven is older' })

    answer = { ...answer, edited: 1, skipped: 2, questions: [], notes: ['Adding a ship needs a new change.'] }
    await user.click(within(form).getByRole('button', { name: 'Ask' }))
    expect(await within(form).findByText(/The assistant edited 1 change and skipped 2 changes\./)).toBeInTheDocument()
    expect(within(form).getByText('Adding a ship needs a new change.')).toBeInTheDocument()
    expect(box).toHaveValue('')
  })

  it('shows the words a change came from, and opens a scene’s studio at them', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({
        operations: [
          operation({
            payload: {
              op: 'create_entity',
              id: 'tmp:a',
              type: 'Location',
              name: 'The quay',
              evidence: [{ source_artefact_id: 'srv_3', location: 'chars:4-44' }],
            },
          }),
        ],
      }),
      'GET /texts/passage': {
        source_id: 'src_9',
        title: 'Night on the water',
        version_number: 3,
        before: 'Now ',
        quote: 'The lamps along the quay were already lit',
        after: ' when she came down.',
        scene_id: 'pln_7',
      },
    })
    const { container } = renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText('The lamps along the quay were already lit')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See it in the scene' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan/pln_7/write?at=4-44&version=srv_3',
    )
    await expectAccessible(container)
  })

  it('reads a change to the story plan in words, as its own group', async () => {
    mockApi({
      ...entries,
      'GET /plan': {
        items: [{ id: 'pln_beat', kind: 'beat', title: 'The theft is discovered', summary: '', parent_id: null, position: 0, refs: [], sets_up: [], pays_off: [], details: {}, created_revision: 1 }],
        open: [],
      },
      'GET /mutations/mut_1': mutation({
        operations: [
          operation({
            operation_type: 'create_plan_item',
            payload: { op: 'create_plan_item', id: 'tmp:plan-0', kind: 'thread', title: 'Who took the ledgers?', summary: '', parent_id: null, position: 0, refs: [], sets_up: [], pays_off: [], details: {} },
          }),
          operation({
            ordinal: 1,
            operation_type: 'create_plan_item',
            payload: {
              op: 'create_plan_item',
              id: 'tmp:plan-1',
              kind: 'scene',
              title: 'The empty vault',
              summary: 'Mara finds the ledgers gone.',
              parent_id: 'pln_beat',
              position: 0,
              refs: [{ entity_id: 'ent_mara', role: 'point_of_view' }],
              sets_up: ['tmp:plan-0'],
              pays_off: [],
              details: {},
            },
          }),
        ],
      }),
    })
    const { container } = renderInProject(<ReviewDetail />, at)

    const plan = await screen.findByRole('region', { name: 'Story plan' })
    expect(within(plan).getByText('Plan scene “The empty vault”')).toBeInTheDocument()
    const scene = within(plan).getByText('Plan scene “The empty vault”').closest('li') as HTMLElement
    expect(within(scene).getByRole('row', { name: /What happens/ })).toHaveTextContent('Mara finds the ledgers gone.')
    expect(within(scene).getByRole('row', { name: /Under/ })).toHaveTextContent('The theft is discovered')
    expect(within(scene).getByRole('row', { name: /Who and where/ })).toHaveTextContent('Mara Venn (point of view)')
    expect(within(scene).getByRole('row', { name: /Opens/ })).toHaveTextContent('Who took the ledgers?')
    await expectAccessible(container)
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
    await user.click(screen.getByRole('button', { name: 'Edit change 1 as stored' }))
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
    expect(await screen.findByText('Added to your story as version 4.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /canon/ })).not.toBeInTheDocument()
    first.unmount()

    mockApi({ ...entries, 'GET /mutations/mut_1': mutation() })
    renderInProject(<ReviewDetail />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByText(/You can read this project but not change it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Skip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Discard these changes' })).not.toBeInTheDocument()
  })
})

describe('ReviewDetail and story time', () => {
  it('offers keeping both values, each for its own time, when one replaced the other', async () => {
    mockApi({ ...entries, 'GET /mutations/mut_1': mutation(), 'GET /timeline': { unit: 'year', placed: [], unplaced: [], spans: [], issues: [] } })
    renderInProject(<ReviewDetail />, {
      ...at,
      state: {
        fromAssistant: true,
        understood: 'Mara is in Saltmere.',
        notes: ['Mara can be “located at” only one thing at a time, so the current one is ended.'],
      },
    })
    expect(await screen.findByText(/you can keep both instead/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Timeline' })).toHaveAttribute('href', '/projects/prj_1/timeline')
  })

  it('shows what the changes touch before they are added, and not once they are', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation(),
      'GET /mutations/mut_1/impact': {
        about: ['Mara Venn is changed'],
        affected: [
          {
            kind: 'scene',
            level: 'affected',
            id: 'pln_1',
            title: 'The Docks',
            reasons: ['Mara Venn is in it'],
            plan_id: 'pln_1',
            count: 0,
          },
        ],
        may_be_affected: [],
        not_checked: ['Checks and notes in the scene studio.'],
      },
    })
    renderInProject(<ReviewDetail />, at)
    const touches = await screen.findByRole('region', { name: 'What this change touches' })
    expect(await within(touches).findByRole('link', { name: 'The Docks' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan#plan-pln_1',
    )
    expect(within(touches).getByText('Mara Venn is in it')).toBeInTheDocument()
  })

  it('leaves out what the changes touch once they are part of the story', async () => {
    const api = mockApi({ ...entries, 'GET /mutations/mut_1': mutation({ status: 'committed', committed_revision: 4 }) })
    renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText(/Added to your story as version 4/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'What this change touches' })).not.toBeInTheDocument()
    expect(api.find('GET', '/impact')).toHaveLength(0)
  })

  it('says whether a stale change can move on as it is, from what changed since', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ is_stale: true, head_revision: 6, changed_since: [] }),
    })
    renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText(/Nothing it touches has changed since/)).toBeInTheDocument()
  })

  it('names what changed since, when the change touches it', async () => {
    mockApi({
      ...entries,
      'GET /mutations/mut_1': mutation({ is_stale: true, head_revision: 6, changed_since: ['ent_mara'] }),
    })
    renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText(/Since then, Mara Venn changed/)).toBeInTheDocument()
  })
})
