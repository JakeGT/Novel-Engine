import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, isApiError } from '../api/client'
import { expectAccessible, mockApi, problem, renderInProject, VIEWER } from '../test/harness'
import { CheckingChoice, CheckingStatus, SpendConfirmHost, SpendingCard } from './AssistantSpending'

afterEach(() => vi.unstubAllGlobals())

const settings = {
  check_profile: 'on_request',
  scene_tokens: 40000,
  check_minutes: 15,
  confirm_above_tokens: null,
  conversation_tokens: null,
  workspace_confirm_above_tokens: null,
  workspace_conversation_tokens: null,
  effective_confirm_above_tokens: null,
  effective_conversation_tokens: null,
}

describe('CheckingChoice', () => {
  it('states what each choice costs for this scene before choosing, and saves the choice', async () => {
    const api = mockApi({
      'GET /assistant-settings': settings,
      'GET /studio/check-estimate': { prompt_tokens: 6800, completion_tokens: 1200, total_tokens: 8000, context_words: 4000 },
      'PUT /assistant-settings': (call: { body: unknown }) => ({ ...settings, ...(call.body as object) }),
    })
    const { container } = renderInProject(<CheckingChoice planId="pln_1" />)

    expect(await screen.findByText('Costs nothing on its own.')).toBeInTheDocument()
    expect(
      await screen.findByText(
        'Uses about 8,000 tokens each check of this scene, and never more than 40,000 tokens a day for one scene.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/up to 5 checks and 40,000 tokens a day for one scene/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Only when I ask/ })).toBeChecked()
    await expectAccessible(container)

    await userEvent.click(screen.getByRole('radio', { name: /Thorough/ }))
    await userEvent.selectOptions(screen.getByLabelText('After saving, at most once every'), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Save how much it checks' }))

    await waitFor(() => expect(api.find('PUT', '/assistant-settings')).toHaveLength(1))
    expect(api.find('PUT', '/assistant-settings')[0].body).toMatchObject({
      check_profile: 'thorough',
      check_minutes: 30,
      scene_tokens: 40000,
    })
    expect(await screen.findByText(/Thorough checking is on for this story/)).toBeInTheDocument()
  })

  it('lets a reader see the choice but not change it', async () => {
    mockApi({ 'GET /assistant-settings': { ...settings, check_profile: 'balanced' } })
    renderInProject(<CheckingChoice />, { context: { access: VIEWER } })
    expect(await screen.findByRole('radio', { name: /Balanced/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Thorough/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save how much it checks' })).not.toBeInTheDocument()
  })
})

describe('CheckingStatus', () => {
  it('says what checks on their own used here, and why one did not start', async () => {
    const { container } = renderInProject(
      <CheckingStatus
        checking={{ profile: 'balanced', scene_tokens: 40000, spent_tokens: 12500, check_minutes: 15, next_after_save: null }}
        automatic="scene_allowance"
        onChange={() => undefined}
      />,
    )
    expect(screen.getByText(/12,500 of 40,000 tokens used for this scene today/)).toBeInTheDocument()
    expect(
      screen.getByText(/Not checked on its own this time\. This scene has used today’s allowance/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change how much it checks' })).toBeInTheDocument()
    await expectAccessible(container)
  })
})

describe('SpendingCard', () => {
  it('sets when to be asked first and says when the workspace limit is lower', async () => {
    const api = mockApi({
      'GET /assistant-settings': { ...settings, workspace_conversation_tokens: 100000 },
      'PUT /assistant-settings': (call: { body: unknown }) => ({ ...settings, ...(call.body as object) }),
    })
    const { container } = renderInProject(<SpendingCard />)
    expect(
      await screen.findByText(/Your workspace allows 100,000 tokens a conversation/),
    ).toBeInTheDocument()
    await expectAccessible(container)

    await userEvent.selectOptions(
      screen.getByLabelText('Ask me first when one request would use more than'),
      '25000',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save spending limits' }))
    await waitFor(() => expect(api.find('PUT', '/assistant-settings')).toHaveLength(1))
    expect(api.find('PUT', '/assistant-settings')[0].body).toMatchObject({
      confirm_above_tokens: 25000,
      conversation_tokens: null,
    })
  })
})

describe('Asking before a large request', () => {
  const scope = { workspaceId: 'ws_1', projectId: 'prj_1' }
  const refusal = problem(
    409,
    'needs_confirmation',
    'Checking this scene will use about 64,000 tokens, more than the 50,000 you asked to be asked about first.',
    { estimated_tokens: '64000', threshold_tokens: '50000' },
  )

  it('asks, then sends the same request again saying the size was agreed', async () => {
    let first = true
    const mock = mockApi({
      'POST /studio/reviews': () => {
        if (first) {
          first = false
          return refusal
        }
        return { id: 'job_1', kind: 'scene_review', status: 'queued' }
      },
    })
    const { container } = render(<SpendConfirmHost />)
    const sent = api.startReview(scope, 'pln_1', null)

    expect(await screen.findByRole('heading', { name: 'Use about 64,000 tokens?' })).toBeInTheDocument()
    expect(screen.getByText(/Checking this scene will use about 64,000 tokens/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing has been sent yet/)).toBeInTheDocument()
    // The safe choice has the focus.
    expect(screen.getByRole('button', { name: 'Don’t send' })).toHaveFocus()
    await expectAccessible(container.ownerDocument.body)

    await userEvent.click(screen.getByRole('button', { name: 'Send it (about 64,000 tokens)' }))
    await expect(sent).resolves.toMatchObject({ id: 'job_1' })
    const calls = mock.find('POST', '/studio/reviews')
    expect(calls).toHaveLength(2)
    expect(calls[0].headers?.get('X-Confirm-Tokens')).toBeNull()
    expect(calls[1].headers?.get('X-Confirm-Tokens')).toBe('64000')
  })

  it('sends nothing when the author says no', async () => {
    const mock = mockApi({ 'POST /studio/reviews': refusal })
    render(<SpendConfirmHost />)
    const sent = api.startReview(scope, 'pln_1', null).catch((error: unknown) => error)
    await userEvent.click(await screen.findByRole('button', { name: 'Don’t send' }))
    const error = await sent
    expect(isApiError(error, 'not_confirmed')).toBe(true)
    expect(mock.find('POST', '/studio/reviews')).toHaveLength(1)
  })
})
