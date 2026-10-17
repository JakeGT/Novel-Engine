import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { GodHand } from './GodHand'

afterEach(() => vi.unstubAllGlobals())

const proposal = {
  mutation_id: 'mut_8',
  status: 'needs_review',
  operation_count: 3,
  session_id: 'chg_1',
  understood: 'Mara moves to Saltmere.',
  questions: [],
  notes: ['The current location is ended.'],
  discarded: [],
  validation_ok: true,
  validation_issues: [],
}

describe('GodHand', () => {
  it('turns one instruction into changes to review, never straight into canon', async () => {
    const api = mockApi({ 'POST /assist/changes': proposal })
    const onClose = vi.fn()
    const user = userEvent.setup()
    const { container } = renderInProject(<GodHand entity={entity()} onClose={onClose} />)

    const dialog = screen.getByRole('dialog', { name: 'Change anything' })
    await user.click(within(dialog).getByRole('button', { name: 'Show me what would change' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Say what should change')
    expect(api.find('POST', '/assist/changes')).toHaveLength(0)
    await expectAccessible(container)

    // An example fills the box; the author can also just type.
    await user.click(within(dialog).getByRole('button', { name: 'Move the Harbor Guild to Saltmere.' }))
    const box = within(dialog).getByLabelText('What should change?')
    await user.clear(box)
    await user.type(box, 'She lives in Saltmere now.')
    await user.click(within(dialog).getByRole('button', { name: 'Show me what would change' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_8'),
    )
    // The entry being looked at goes along, so "she" can be resolved; the head is re-read.
    expect(api.find('POST', '/assist/changes')[0].body).toEqual({
      instruction: 'She lives in Saltmere now.',
      entity_id: 'ent_mara',
      session_id: null,
      expected_revision: 5,
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('asks instead of guessing, and sends the answer with the instruction', async () => {
    let calls = 0
    const api = mockApi({
      'POST /assist/changes': () => {
        calls += 1
        return calls === 1
          ? { ...proposal, mutation_id: null, status: null, operation_count: 0, questions: ['Which harbor do you mean?'] }
          : proposal
      },
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderInProject(<GodHand onClose={onClose} />)

    await user.type(screen.getByLabelText('What should change?'), 'Burn the harbor down.')
    await user.click(screen.getByRole('button', { name: 'Show me what would change' }))

    expect(await screen.findByText(/Which harbor do you mean\?/)).toBeInTheDocument()
    // Nothing was proposed, so the dialog stays open with what was typed.
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('What should change?')).toHaveValue('Burn the harbor down.')

    await user.type(screen.getByLabelText('Your answer'), 'Lantern Harbor.')
    await user.click(screen.getByRole('button', { name: 'Show me what would change' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.find('POST', '/assist/changes')[1].body).toMatchObject({
      instruction: 'Burn the harbor down.\n\nTo be clear: Lantern Harbor.',
      entity_id: null,
      // The answer continues the conversation the question was asked in.
      session_id: 'chg_1',
    })
  })

  it('carries on a conversation where the assistant had asked something', async () => {
    const api = mockApi({ 'POST /assist/changes': proposal })
    const user = userEvent.setup()
    renderInProject(
      <GodHand
        onClose={vi.fn()}
        resume={{
          id: 'chg_7',
          kind: 'change',
          status: 'open',
          actor_user_id: 'usr_1',
          description: 'Burn the harbor down.',
          entity_type: null,
          name: null,
          entity_id: 'ent_mara',
          questions: [{ id: 'q1', question: 'Which harbor do you mean?', category: '' }],
          answers: [],
          understood: 'Unclear.',
          notes: [],
          suggestions: [],
          mutation_id: null,
          created_at: '2026-10-09T10:00:00Z',
          updated_at: '2026-10-09T10:00:00Z',
        }}
      />,
    )

    expect(screen.getByLabelText('What should change?')).toHaveValue('Burn the harbor down.')
    expect(screen.getByText(/Which harbor do you mean\?/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Your answer'), 'Lantern Harbor.')
    await user.click(screen.getByRole('button', { name: 'Show me what would change' }))

    await waitFor(() => expect(api.find('POST', '/assist/changes')).toHaveLength(1))
    expect(api.find('POST', '/assist/changes')[0].body).toEqual({
      instruction: 'Burn the harbor down.\n\nTo be clear: Lantern Harbor.',
      entity_id: 'ent_mara',
      session_id: 'chg_7',
      expected_revision: 5,
    })
  })

  it('says plainly when no AI provider is connected or nothing would change', async () => {
    mockApi({ 'POST /assist/changes': problem(503, 'capability_unavailable', 'No provider.') })
    const user = userEvent.setup()
    const first = renderInProject(<GodHand onClose={vi.fn()} />)
    await user.type(screen.getByLabelText('What should change?'), 'Anything.')
    await user.click(screen.getByRole('button', { name: 'Show me what would change' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The assistant isn’t available right now')
    first.unmount()

    mockApi({
      'POST /assist/changes': problem(400, 'validation_error', 'That instruction did not lead to any change.'),
    })
    renderInProject(<GodHand onClose={vi.fn()} />)
    await user.type(screen.getByLabelText('What should change?'), 'Mara is Mara.')
    await user.click(screen.getByRole('button', { name: 'Show me what would change' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('did not lead to any change')
  })
  it('points to where a request is done when it is not a change to the story', async () => {
    mockApi({
      'POST /assist/changes': {
        ...proposal,
        mutation_id: null,
        status: null,
        operation_count: 0,
        elsewhere: [{ place: 'history', what: 'Go back to the version from yesterday.' }],
      },
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderInProject(<GodHand onClose={onClose} />)

    const dialog = screen.getByRole('dialog', { name: 'Change anything' })
    await user.type(within(dialog).getByLabelText('What should change?'), 'Undo yesterday.')
    await user.click(within(dialog).getByRole('button', { name: 'Show me what would change' }))

    expect(await within(dialog).findByText(/That is not a change to your story bible/)).toBeInTheDocument()
    expect(within(dialog).getByText('Go back to the version from yesterday.')).toBeInTheDocument()
    const link = within(dialog).getByRole('link', { name: 'Open History' })
    expect(link).toHaveAttribute('href', '/projects/prj_1/history')
    await user.click(link)
    expect(onClose).toHaveBeenCalled()
  })
  it('shows what going back would change, and carries an invitation to the People page', async () => {
    mockApi({
      'POST /assist/changes': {
        ...proposal,
        mutation_id: null,
        status: null,
        operation_count: 0,
        elsewhere: [
          { place: 'history', what: 'Go back to version 2.' },
          { place: 'people', what: 'Invite Sam as an editor.' },
        ],
        back_to_version: 2,
        invite: { role: 'editor', email: 'sam@example.com' },
      },
      'POST /reverts/preview': {
        from_revision: 2,
        to_revision: 3,
        head_revision: 3,
        operations: [{ op: 'retire_entity', id: 'ent_mara' }],
        changes: [],
        conflicts: [],
        summary: 'revert r3',
      },
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
    })
    const user = userEvent.setup()
    renderInProject(<GodHand onClose={vi.fn()} />)

    const dialog = screen.getByRole('dialog', { name: 'Change anything' })
    await user.type(within(dialog).getByLabelText('What should change?'), 'Undo the last one and invite Sam.')
    await user.click(within(dialog).getByRole('button', { name: 'Show me what would change' }))

    expect(await within(dialog).findByRole('link', { name: 'Open People with this filled in' })).toHaveAttribute(
      'href',
      '/projects/prj_1/people',
    )
    // Nothing is undone by asking: the author is shown the restore first.
    await user.click(within(dialog).getByRole('button', { name: 'See what going back to version 2 would change' }))
    const restore = await screen.findByRole('dialog', { name: 'Go back to version 2?' })
    expect(await within(restore).findByText(/Archive/)).toBeInTheDocument()

    // Changing one's mind returns to the request, as it was.
    await user.click(within(restore).getByRole('button', { name: /Keep|Cancel|Close/ }))
    expect(await screen.findByRole('dialog', { name: 'Change anything' })).toBeInTheDocument()
    expect(screen.getByLabelText('What should change?')).toHaveValue('Undo the last one and invite Sam.')
  })
})
