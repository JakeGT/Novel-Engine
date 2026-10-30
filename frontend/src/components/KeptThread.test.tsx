import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AssistThread } from '../api/client'
import { runSentence } from '../lib/threads'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { GodHand } from './GodHand'
import { GroundingList, SuggestionRecord } from './SuggestionRecord'

afterEach(() => vi.unstubAllGlobals())

const turn = (position: number, role: 'author' | 'assistant', body: string, extra = {}) => ({
  position,
  role,
  body,
  questions: [],
  answers: [],
  notes: [],
  mutation_id: null,
  operation_count: 0,
  created_at: '2026-10-21T10:00:00Z',
  ...extra,
})

const thread: AssistThread = {
  kind: 'change',
  id: 'thr_1',
  turns: [
    turn(1, 'author', 'Burn the harbor down.'),
    turn(2, 'assistant', 'Which one?', { questions: ['Which harbor do you mean?'] }),
    turn(3, 'author', 'The old one.'),
    turn(4, 'assistant', 'The old harbor burns.', { mutation_id: 'mut_4', operation_count: 2 }),
    turn(5, 'author', 'And the guild leaves.'),
    turn(6, 'assistant', 'The guild moves to Saltmere.'),
  ],
  open_questions: [],
  tokens_used: 400,
  updated_at: '2026-10-21T10:00:00Z',
}

describe('the kept conversation in Change anything', () => {
  it('shows what was said before, leads to its suggestions, and clears only when asked twice', async () => {
    const api = mockApi({ 'GET /assist/threads/change': thread, 'DELETE /assist/threads/change': { cleared: true } })
    const user = userEvent.setup()
    const { container } = renderInProject(<GodHand onClose={vi.fn()} />)

    const earlier = await screen.findByRole('region', { name: 'Earlier in this conversation' })
    // The last few turns are shown; the whole conversation is one click away.
    expect(within(earlier).queryByText('Burn the harbor down.')).not.toBeInTheDocument()
    expect(within(earlier).getByText('The old harbor burns.')).toBeInTheDocument()
    expect(within(earlier).getByRole('link', { name: 'Open them in review' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_4',
    )
    await user.click(within(earlier).getByRole('button', { name: 'Show the whole conversation (6 turns)' }))
    expect(within(earlier).getByText('Burn the harbor down.')).toBeInTheDocument()
    expect(within(earlier).getByText('Which harbor do you mean?')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(earlier).getByRole('button', { name: 'Clear and start again' }))
    const ask = within(earlier).getByRole('group', { name: 'Clear this conversation?' })
    expect(ask).toHaveTextContent('cannot be brought back')
    await user.click(within(ask).getByRole('button', { name: 'Keep the conversation' }))
    expect(api.find('DELETE', '/assist/threads/change')).toHaveLength(0)

    await user.click(within(earlier).getByRole('button', { name: 'Clear and start again' }))
    await user.click(within(earlier).getByRole('button', { name: 'Clear conversation' }))
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Earlier in this conversation' })).not.toBeInTheDocument(),
    )
    expect(api.find('DELETE', '/assist/threads/change')).toHaveLength(1)
  })

  it('shows nothing when there is no conversation yet, and no clearing to a reader', async () => {
    mockApi({ 'GET /assist/threads/change': { ...thread, id: null, turns: [] } })
    renderInProject(<GodHand onClose={vi.fn()} />)
    await screen.findByRole('dialog', { name: 'Change anything' })
    expect(screen.queryByText('Earlier in this conversation')).not.toBeInTheDocument()
  })

  it('never offers a reader the clearing', async () => {
    mockApi({ 'GET /assist/threads/change': thread })
    renderInProject(<GodHand onClose={vi.fn()} />, { context: { access: VIEWER, canWrite: false } })
    await screen.findByText('The guild moves to Saltmere.')
    expect(screen.queryByRole('button', { name: 'Clear and start again' })).not.toBeInTheDocument()
  })
})

describe('how suggestions fared', () => {
  it('says how many were kept once decided, and never guesses before', async () => {
    mockApi({
      'GET /assist/runs': {
        items: [
          {
            id: 'run_2',
            kind: 'links',
            source: 'meaning',
            suggested: 3,
            kept: null,
            precision: null,
            grounded: null,
            invented: null,
            groundedness: null,
            decided: false,
            mutation_id: 'mut_2',
            prompt_version: 'suggest-links/3',
            created_at: new Date().toISOString(),
            details: { candidates: 4 },
          },
          {
            id: 'run_1',
            kind: 'links',
            source: 'listing',
            suggested: 4,
            kept: 3,
            precision: 0.75,
            grounded: null,
            invented: null,
            groundedness: null,
            decided: true,
            mutation_id: 'mut_1',
            prompt_version: 'suggest-links/3',
            created_at: new Date().toISOString(),
            details: {},
          },
        ],
      },
    })
    const { container } = renderInProject(<SuggestionRecord kind="links" title="Earlier suggestions" />)
    expect(
      await screen.findByText('3 suggestions · waiting for your review · close pairs found by meaning first'),
    ).toBeInTheDocument()
    expect(screen.getByText('4 suggestions · 3 kept (75%) · from the listing')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('never counts what a rule kept as kept by the author', () => {
    expect(
      runSentence({
        id: 'run_3',
        kind: 'links',
        source: 'listing',
        suggested: 2,
        kept: 0,
        kept_for_you: 2,
        precision: null,
        grounded: null,
        invented: null,
        groundedness: null,
        decided: true,
        mutation_id: 'mut_3',
        prompt_version: 'suggest-links/3',
        created_at: new Date().toISOString(),
        details: { kept_for_you: 2 },
      }),
    ).toBe('2 suggestions · 2 kept for you by your rule · from the listing')
  })

  it('says which parts of a plan quote the author and which are the assistant’s own', () => {
    renderInProject(
      <GroundingList
        items={[
          { title: 'The search', quote: 'Wren looks for her brother', grounded: true },
          { title: 'A letter arrives', quote: null, grounded: false },
        ]}
      />,
    )
    expect(screen.getByText(/1 of 2 quote what you wrote/)).toBeInTheDocument()
    expect(screen.getByText('Wren looks for her brother')).toBeInTheDocument()
    expect(screen.getByText(/The assistant’s own suggestion/)).toBeInTheDocument()
  })
})
