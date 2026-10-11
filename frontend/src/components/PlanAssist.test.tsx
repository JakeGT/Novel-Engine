import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, problem, renderInProject, reply } from '../test/harness'
import { NextDirections, PlanAssist } from './PlanAssist'
import { PlanNudge } from './PlanNudge'

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const proposal = {
  mutation_id: 'mut_5',
  operation_count: 4,
  session_id: 'pnv_1',
  understood: 'A pilot is betrayed.',
  notes: ['Left open: How the arc ends has not been decided.'],
  discarded: [],
  validation_ok: true,
}

describe('PlanAssist', () => {
  it('asks first when wanted, then drafts a plan to review with the answers', async () => {
    const api = mockApi({
      'POST /assist/plan/questions': {
        session_id: 'pnv_1',
        questions: [
          { id: 'stakes', question: 'What does Mara stand to lose?', category: '' },
          { id: 'whose', question: 'Whose story is it?', category: '' },
        ],
      },
      'POST /assist/plan/proposals': reply(201, proposal),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PlanAssist onClose={vi.fn()} />)

    const form = screen.getByRole('form', { name: 'Plan with the assistant' })
    expect(form).toHaveTextContent('It does not make the story up for you.')
    await user.click(within(form).getByRole('button', { name: 'Draft the plan' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('Say something about the story first.')
    await expectAccessible(container)

    await user.type(within(form).getByLabelText('What do you know so far?'), 'A betrayal in the guild.')
    await user.click(within(form).getByLabelText(/Arcs and their turning points/))
    await user.click(within(form).getByRole('button', { name: 'Ask me questions first' }))
    await user.type(await within(form).findByLabelText('What does Mara stand to lose?'), 'Her licence.')
    await user.click(within(form).getByRole('button', { name: 'Draft the plan' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_5'),
    )
    expect(api.find('POST', '/assist/plan/questions')[0].body).toEqual({
      description: 'A betrayal in the guild.',
      depth: 'beats',
      about_id: null,
    })
    // Only what was answered goes along; the unanswered question is simply left out.
    expect(api.find('POST', '/assist/plan/proposals')[0].body).toEqual({
      description: 'A betrayal in the guild.',
      depth: 'beats',
      about_id: null,
      session_id: 'pnv_1',
      answers: [{ question_id: 'stakes', question: 'What does Mara stand to lose?', answer: 'Her licence.' }],
      expected_revision: 5,
    })
  })

  it('falls back to planning by hand when there is no assistant', async () => {
    mockApi({ 'POST /assist/plan/proposals': problem(503, 'capability_unavailable', 'No provider.') })
    const onManual = vi.fn()
    const user = userEvent.setup()
    renderInProject(<PlanAssist onClose={vi.fn()} onManual={onManual} />)

    await user.type(screen.getByLabelText('What do you know so far?'), 'A betrayal.')
    await user.click(screen.getByRole('button', { name: 'Draft the plan' }))
    expect(await screen.findByText(/The assistant isn’t available right now/)).toBeInTheDocument()
    // What was typed is still there, and the by-hand path is one click away.
    expect(screen.getByLabelText('What do you know so far?')).toHaveValue('A betrayal.')
    await user.click(screen.getByRole('button', { name: 'Add to the plan by hand' }))
    expect(onManual).toHaveBeenCalled()
  })
})

describe('NextDirections', () => {
  it('offers directions only when asked, and plans only the one that is picked', async () => {
    const draft = { understood: 'Mara confronts Tobin', new_entries: [], items: [{ ref: 's', kind: 'scene', title: 'The accusation' }], gaps: [] }
    const api = mockApi({
      'POST /assist/plan/directions': {
        directions: [
          { title: 'Mara confronts Tobin', what_happens: 'She accuses him.', what_it_changes: ['Their friendship ends.'], adds: 1, draft },
          { title: 'Mara leaves', what_happens: 'She goes quietly.', what_it_changes: [], adds: 2, draft: { ...draft, understood: 'Mara leaves' } },
        ],
      },
      'POST /assist/plan/directions/choose': reply(201, { ...proposal, mutation_id: 'mut_6', notes: [] }),
    })
    const user = userEvent.setup()
    renderInProject(<NextDirections onClose={vi.fn()} />)

    const dialog = screen.getByRole('dialog', { name: 'What could happen next?' })
    expect(dialog).toHaveTextContent('suggestions to choose from, change or ignore')
    expect(api.find('POST', '/assist/plan/directions')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Show me some directions' }))

    expect(await within(dialog).findByRole('heading', { name: 'Mara confronts Tobin' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Their friendship ends.')
    expect(dialog).toHaveTextContent('Suggests 2 changes for you to review.')
    expect(within(dialog).getByRole('button', { name: 'None of these' })).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Plan this direction: Mara confronts Tobin' }))
    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_6'),
    )
    expect(api.find('POST', '/choose')[0].body).toEqual({ draft, expected_revision: 5 })
  })
})

describe('PlanNudge', () => {
  it('offers planning once the story bible has enough in it, and can be turned down for good', async () => {
    mockApi({ 'GET /plan': { items: [], open: [] } })
    const user = userEvent.setup()
    const first = renderInProject(<PlanNudge entryCount={2} />)
    await waitFor(() => expect(screen.queryByRole('region')).not.toBeInTheDocument())
    first.unmount()

    const second = renderInProject(<PlanNudge entryCount={6} />)
    const offer = await screen.findByRole('region', { name: 'Ready to plan the story?' })
    expect(offer).toHaveTextContent('Planning is optional')
    await user.click(within(offer).getByRole('button', { name: 'Not for this story' }))
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    second.unmount()

    renderInProject(<PlanNudge entryCount={6} />)
    await waitFor(() => expect(screen.queryByRole('region')).not.toBeInTheDocument())
  })

  it('lists what an existing plan still needs', async () => {
    mockApi({
      'GET /plan': {
        items: [{ id: 'pln_s', kind: 'scene', title: 'The lamp room', summary: '', parent_id: null, position: 0, refs: [], sets_up: [], pays_off: [], details: {}, created_revision: 1 }],
        open: [{ plan_id: 'pln_s', code: 'needs_place', message: 'This scene needs a place.' }],
      },
    })
    renderInProject(<PlanNudge entryCount={1} />)
    const card = await screen.findByRole('region', { name: 'Your plan has 1 gap' })
    expect(card).toHaveTextContent('The lamp room')
    expect(card).toHaveTextContent('This scene needs a place.')
    expect(within(card).getByRole('link', { name: 'Open the story plan' })).toHaveAttribute('href', '/projects/prj_1/plan')
  })
})
