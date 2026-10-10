import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AssistSession } from '../api/client'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { OpenConversations } from './Conversations'

afterEach(() => vi.unstubAllGlobals())

function session(overrides: Partial<AssistSession> = {}): AssistSession {
  return {
    id: 'brf_1',
    kind: 'create',
    status: 'open',
    actor_user_id: 'usr_1',
    description: 'Mara Venn is a harbor pilot in Saltmere.',
    entity_type: 'Character',
    name: 'Mara Venn',
    entity_id: null,
    questions: [
      { id: 'q1', question: 'Where does she live?', category: 'place' },
      { id: 'q2', question: 'Who does she trust?', category: 'people' },
    ],
    answers: [{ question_id: 'q1', question: 'Where does she live?', answer: 'Saltmere.' }],
    understood: '',
    notes: [],
    mutation_id: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('OpenConversations', () => {
  it('offers unfinished conversations in the author’s own words, and reopens one', async () => {
    mockApi({
      'GET /assist/sessions': {
        items: [
          session(),
          session({
            id: 'chg_2',
            kind: 'change',
            description: 'Burn the harbor down.',
            questions: [{ id: 'q1', question: 'Which harbor?', category: '' }],
            answers: [],
          }),
        ],
      },
    })
    const resume = vi.fn()
    const user = userEvent.setup()
    const { container } = renderInProject(<OpenConversations />, { context: { resume } })

    const section = await screen.findByRole('region', { name: 'Pick up where you left off' })
    const rows = within(section).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('“Mara Venn is a harbor pilot in Saltmere.”')
    expect(rows[0]).toHaveTextContent('Adding to the story bible · 1 of 2 questions answered')
    expect(rows[1]).toHaveTextContent('Changing something · the assistant asked you something')
    expect(container).not.toHaveTextContent('brf_1')
    await expectAccessible(container)

    await user.click(within(rows[1]).getByRole('button', { name: 'Continue' }))
    expect(resume).toHaveBeenCalledWith(expect.objectContaining({ id: 'chg_2' }))
  })

  it('asks before putting one away, and keeps it by default', async () => {
    const api = mockApi({
      'GET /assist/sessions': { items: [session()] },
      'POST /assist/sessions/brf_1/close': session({ status: 'closed' }),
    })
    const user = userEvent.setup()
    renderInProject(<OpenConversations />)

    await user.click(await screen.findByRole('button', { name: 'Put away' }))
    const dialog = screen.getByRole('dialog', { name: 'Put this conversation away?' })
    expect(dialog).toHaveTextContent('cannot be brought back')
    expect(within(dialog).getByRole('button', { name: 'Keep it here' })).toHaveFocus()
    expect(api.find('POST', '/close')).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: 'Put it away' }))
    await waitFor(() => expect(api.find('POST', '/assist/sessions/brf_1/close')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('shows nothing when there is nothing waiting, and asks nothing for a reader', async () => {
    const api = mockApi({ 'GET /assist/sessions': { items: [] } })
    const first = renderInProject(<OpenConversations />)
    await waitFor(() => expect(api.find('GET', '/assist/sessions')).toHaveLength(1))
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    first.unmount()

    const reader = mockApi({})
    renderInProject(<OpenConversations />, { context: { access: VIEWER } })
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(reader.calls).toHaveLength(0)
  })
})
