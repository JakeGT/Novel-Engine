import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { KeptItem, KeptSettings } from '../api/client'
import { KeepDecisionNote, KeptSinceNotice } from '../components/KeptForYou'
import { entity } from '../test/fixtures'
import {
  expectAccessible,
  expectPlainLanguage,
  mockApi,
  problem,
  renderInProject,
  reply,
  VIEWER,
} from '../test/harness'
import { KeptPage } from './KeptPage'

afterEach(() => vi.unstubAllGlobals())

const settings = (overrides: Partial<KeptSettings> = {}): KeptSettings => ({
  rules: [
    {
      kind: 'new_entry',
      name: 'New entries',
      description: 'A new person, place or thing in the story bible.',
      on: true,
      hand_approvals: 4,
      since: '2026-10-18T09:00:00Z',
    },
    {
      kind: 'first_details',
      name: 'Details for entries that have none',
      description: 'Details on an entry that has none yet.',
      on: false,
      hand_approvals: 1,
      since: null,
    },
    {
      kind: 'new_fact',
      name: 'New facts that change nothing else',
      description: 'A new fact that contradicts nothing and replaces nothing.',
      on: false,
      hand_approvals: 3,
      since: null,
    },
  ],
  allow_clients: false,
  workspace_forbidden: false,
  hand_approvals_needed: 3,
  size_limit: 12,
  daily_limit: 50,
  always_waits: ['Removing, archiving or ending anything', 'Undoing earlier changes'],
  kept_since_seen: 2,
  ...overrides,
})

const kept: KeptItem = {
  mutation_id: 'mut_9',
  kinds: ['new_entry'],
  note: 'Kept for you under your rule “New entries”.',
  kept_at: '2026-10-18T10:00:00Z',
  revision: 7,
  operations: [
    {
      ordinal: 0,
      operation_type: 'create_entity',
      payload: { id: 'tmp:n', type: 'Character', name: 'Ruth' },
    },
  ],
  undo_mutation_id: null,
  undo_status: null,
}

const base = {
  'GET /entities': { items: [entity({ id: 'ent_mara', name: 'Mara' })], limit: 200, offset: 0 },
  'POST /kept-for-you/seen': reply(204),
}
const at = { path: 'review/kept', at: 'review/kept' }

describe('KeptPage', () => {
  it('shows the rules with how often each was approved by hand, and what was kept', async () => {
    const api = mockApi({
      ...base,
      'GET /kept-for-you/settings': settings(),
      'GET /kept-for-you': { items: [kept] },
    })
    const { container } = renderInProject(<KeptPage />, at)

    expect(await screen.findByRole('checkbox', { name: /New entries/ })).toBeChecked()
    const details = screen.getByRole('checkbox', { name: /Details for entries that have none/ })
    // Not approved by hand often enough yet: it cannot be switched on, and says why.
    expect(details).toBeDisabled()
    expect(screen.getByText(/approved 1 of 3 by hand. Approve 2 more/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /New facts/ })).toBeEnabled()
    expect(screen.queryByRole('checkbox', { name: /other assistants you connected/ })).not.toBeInTheDocument()
    expect(screen.getByText('Undoing earlier changes')).toBeInTheDocument()

    const row = (await screen.findByText('Add character “Ruth”')).closest('li') as HTMLElement
    expect(row).toHaveTextContent('Kept for you under your rule “New entries” ·')
    expect(row).toHaveTextContent('version 7 of your story')
    expect(within(row).getByRole('link', { name: 'See the whole change' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_9',
    )
    // Looking here counts as having seen what was kept.
    await waitFor(() => expect(api.find('POST', '/kept-for-you/seen')).toHaveLength(1))
    expectPlainLanguage(container)
    await expectAccessible(container)
  })

  it('switches a rule on or off at once and says so', async () => {
    const api = mockApi({
      ...base,
      'GET /kept-for-you/settings': settings(),
      'GET /kept-for-you': { items: [] },
      'PUT /kept-for-you/settings': settings({
        rules: settings().rules.map((rule) => (rule.kind === 'new_fact' ? { ...rule, on: true } : rule)),
      }),
    })
    const user = userEvent.setup()
    renderInProject(<KeptPage />, at)

    await user.click(await screen.findByRole('checkbox', { name: /New facts/ }))
    await waitFor(() => expect(api.find('PUT', '/kept-for-you/settings')).toHaveLength(1))
    expect(api.find('PUT', '/kept-for-you/settings')[0].body).toEqual({
      kinds: ['new_entry', 'new_fact'],
      allow_clients: false,
    })
    expect(await screen.findByText('“New facts that change nothing else” will be kept for you from now on.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Switch it all off' }))
    await waitFor(() => expect(api.find('PUT', '/kept-for-you/settings')).toHaveLength(2))
    expect(api.find('PUT', '/kept-for-you/settings')[1].body).toEqual({ kinds: [], allow_clients: false })
    expect(await screen.findByText(/Nothing has been kept for you yet/)).toBeInTheDocument()
  })

  it('undo suggests the undo and links to it in review', async () => {
    let undone = false
    const api = mockApi({
      ...base,
      'GET /kept-for-you/settings': settings(),
      'GET /kept-for-you': () => ({
        items: [undone ? { ...kept, undo_mutation_id: 'mut_10', undo_status: 'needs_review' } : kept],
      }),
      'POST /kept-for-you/mut_9/undo': () => {
        undone = true
        return reply(201, { undo_mutation_id: 'mut_10' })
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<KeptPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Undo' }))
    expect(await screen.findByRole('link', { name: 'Review the undo' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_10',
    )
    expect(screen.getByText(/Undo suggested and waiting for you/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
    expect(api.find('POST', '/undo')).toHaveLength(1)
    await expectAccessible(container)
  })

  it('says when an undo cannot be suggested, next to the change', async () => {
    mockApi({
      ...base,
      'GET /kept-for-you/settings': settings(),
      'GET /kept-for-you': { items: [kept] },
      'POST /kept-for-you/mut_9/undo': problem(
        400,
        'validation_error',
        'Something it added has changed since, so it cannot be undone in one step.',
      ),
    })
    const user = userEvent.setup()
    renderInProject(<KeptPage />, at)
    await user.click(await screen.findByRole('button', { name: 'Undo' }))
    expect(await screen.findByText(/cannot be undone in one step/)).toBeInTheDocument()
  })

  it('tells everyone when a workspace owner turned it off, and readers cannot change it', async () => {
    mockApi({
      ...base,
      'GET /kept-for-you/settings': settings({ workspace_forbidden: true }),
      'GET /kept-for-you': { items: [] },
    })
    const { container } = renderInProject(<KeptPage />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByText(/A workspace owner has turned this off/)).toBeInTheDocument()
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Switch it all off' })).not.toBeInTheDocument()
    await expectAccessible(container)
  })
})

describe('KeptSinceNotice', () => {
  it('counts what was kept since the author last looked', async () => {
    mockApi({ 'GET /kept-for-you/settings': settings() })
    const { container } = renderInProject(<KeptSinceNotice />)
    expect(await screen.findByText(/2 suggestions were kept for you since you last looked/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See what was kept' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/kept',
    )
    await expectAccessible(container)
  })

  it('offers keeping once a kind was approved by hand enough, and only then', async () => {
    const off = settings({ kept_since_seen: 0 }).rules.map((rule) => ({ ...rule, on: false }))
    mockApi({ 'GET /kept-for-you/settings': settings({ kept_since_seen: 0, rules: off }) })
    renderInProject(<KeptSinceNotice />)
    expect(await screen.findByText(/You have approved 4 new entries from the assistant by hand/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Choose what to keep' })).toBeInTheDocument()
  })
})

describe('KeepDecisionNote', () => {
  it('names the rule a suggestion was kept under', async () => {
    mockApi({
      'GET /kept-for-you/decisions/mut_9': {
        mutation_id: 'mut_9',
        how: 'kept',
        kinds: ['new_entry'],
        note: 'Kept for you under your rule “New entries”.',
      },
    })
    renderInProject(<KeepDecisionNote mutationId="mut_9" />)
    expect(await screen.findByText(/Kept for you under your rule “New entries”/)).toBeInTheDocument()
  })

  it('says why a suggestion waited', async () => {
    mockApi({
      'GET /kept-for-you/decisions/mut_8': {
        mutation_id: 'mut_8',
        how: 'waited',
        kinds: [],
        reason_code: 'not_additive',
        reason: 'It includes archiving an entry.',
      },
    })
    const { container } = renderInProject(<KeepDecisionNote mutationId="mut_8" />)
    expect(await screen.findByText(/This was not kept for you: It includes archiving an entry./)).toBeInTheDocument()
    await expectAccessible(container)
  })
})
