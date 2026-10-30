import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AssistThread } from '../api/client'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { PremisePage } from './PremisePage'

afterEach(() => vi.unstubAllGlobals())

const empty: AssistThread = {
  kind: 'premise',
  id: null,
  turns: [],
  open_questions: [],
  tokens_used: 0,
  updated_at: null,
}

const asked: AssistThread = {
  kind: 'premise',
  id: 'thr_p',
  turns: [
    {
      position: 1,
      role: 'author',
      body: 'Wren keeps the books at the mill.',
      questions: [],
      answers: [],
      notes: [],
      mutation_id: null,
      operation_count: 0,
      created_at: '2026-10-21T10:00:00Z',
    },
    {
      position: 2,
      role: 'assistant',
      body: 'A bookkeeper looks for her brother.',
      questions: ["What is her brother's name?", 'Who owns the mill?'],
      answers: [],
      notes: [],
      mutation_id: 'mut_1',
      operation_count: 3,
      created_at: '2026-10-21T10:00:00Z',
    },
  ],
  open_questions: ["What is her brother's name?", 'Who owns the mill?'],
  tokens_used: 300,
  updated_at: '2026-10-21T10:00:00Z',
}

const round = {
  thread_id: 'thr_p',
  understood: 'A bookkeeper looks for her brother.',
  questions: ["What is her brother's name?"],
  mutation_id: 'mut_1',
  status: 'needs_review',
  operation_count: 3,
  discarded: [],
  suggestions: [],
  validation_ok: true,
  validation_issues: [],
  session_id: 'brf_1',
}

describe('PremisePage', () => {
  it('reads the story, asks what it needs, and offers the suggestions for review', async () => {
    let thread = empty
    const api = mockApi({
      'GET /assist/threads/premise': () => thread,
      'POST /assist/premise': () => {
        thread = asked
        return round
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PremisePage />)

    const box = await screen.findByLabelText('Your story, in your own words')
    await user.click(screen.getByRole('button', { name: 'Read it and ask me' }))
    expect(await screen.findByText('Describe your story first.')).toBeInTheDocument()
    expect(api.find('POST', '/assist/premise')).toHaveLength(0)
    await expectAccessible(container)

    await user.type(box, 'Wren keeps the books at the mill.')
    await user.click(screen.getByRole('button', { name: 'Read it and ask me' }))
    expect(await screen.findByRole('link', { name: 'Review the suggestions' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_1',
    )
    expect(screen.getByText('Nothing is added to your story until you approve it.')).toBeInTheDocument()
    expect(api.find('POST', '/assist/premise')[0].body).toEqual({
      premise: 'Wren keeps the books at the mill.',
      answers: [],
      expected_revision: 5,
    })
    // The questions come next, each its own box, any of them skippable.
    expect(await screen.findByLabelText("What is her brother's name?")).toBeInTheDocument()
    expect(screen.getByLabelText('Who owns the mill?')).toBeInTheDocument()
  })

  it('answers in turn, skipping what is left empty, and keeps what was typed when it fails', async () => {
    const api = mockApi({
      'GET /assist/threads/premise': asked,
      'POST /assist/premise': problem(503, 'provider_failure', 'The provider did not answer.'),
    })
    const user = userEvent.setup()
    renderInProject(<PremisePage />)

    await user.click(await screen.findByRole('button', { name: 'Send my answers' }))
    expect(
      await screen.findByText('Answer at least one question, or add more about your story.'),
    ).toBeInTheDocument()

    await user.type(screen.getByLabelText("What is her brother's name?"), 'Tam.')
    await user.click(screen.getByRole('button', { name: 'Send my answers' }))
    await waitFor(() => expect(api.find('POST', '/assist/premise')).toHaveLength(1))
    expect(api.find('POST', '/assist/premise')[0].body).toEqual({
      premise: '',
      answers: [
        { question: "What is her brother's name?", answer: 'Tam.' },
        { question: 'Who owns the mill?', answer: '' },
      ],
      expected_revision: 5,
    })
    expect(await screen.findByText(/isn’t available right now/)).toBeInTheDocument()
    expect(screen.getByLabelText("What is her brother's name?")).toHaveValue('Tam.')
    // What was said before stays in view.
    expect(screen.getByText('Wren keeps the books at the mill.')).toBeInTheDocument()
  })
})
