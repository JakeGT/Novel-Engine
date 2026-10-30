import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReviewFinding } from '../api/client'
import { ReviewDetail } from '../pages/ReviewDetail'
import { entity, mutation } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'

afterEach(() => vi.unstubAllGlobals())

const at = { path: 'review/:mutationId', at: 'review/mut_1' }
const DUPLICATE = 'You already have a location called “Mara Venn-Marsh”.'
const KEY = 'a'.repeat(64)

function finding(overrides: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    ordinal: 1,
    code: 'possible_duplicate',
    message: DUPLICATE,
    severity: 'advisory',
    key: KEY,
    dismissal: null,
    came_back: false,
    ...overrides,
  }
}

const dismissed = finding({
  dismissal: { id: 'rd_1', reason: 'not_a_problem', note: '', created_at: '2026-10-20T10:00:00Z' },
})

describe('Dismissing a finding on the review screen (CON-016)', () => {
  it('dismisses a finding worth a look with a reason, for these changes, and brings it back', async () => {
    let current = { items: [finding()] }
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /assist/sessions': { items: [] },
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/validate': {
        ok: true,
        head_revision: 3,
        issues: [
          { ordinal: 0, code: 'slug_conflict', severity: 'blocking', message: 'That short name is taken.' },
          { ordinal: 1, code: 'possible_duplicate', severity: 'advisory', message: DUPLICATE },
        ],
      },
      'GET /mutations/mut_1/findings': () => current,
      'POST /mutations/mut_1/findings/dismissals': () => (current = { items: [dismissed] }),
      'DELETE /mutations/mut_1/findings/dismissals/rd_1': () => (current = { items: [finding()] }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<ReviewDetail />, at)

    const first = (await screen.findByText('Add character “Iven”')).closest('li') as HTMLElement
    // A finding that must be fixed first is never offered for dismissing.
    expect(await within(first).findByRole('alert')).toHaveTextContent('That short name is taken.')
    expect(within(first).queryByRole('button', { name: /Dismiss/ })).not.toBeInTheDocument()

    const second = screen.getByText('Update “Mara Venn”').closest('li') as HTMLElement
    await user.click(await within(second).findByRole('button', { name: `Dismiss: ${DUPLICATE}` }))
    const form = within(second).getByRole('group', { name: `Dismiss: ${DUPLICATE}` })
    expect(within(form).getByText(/For these changes only/)).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Not a problem' }))
    await expectAccessible(container)
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(api.find('POST', '/findings/dismissals')).toHaveLength(1))
    expect(api.find('POST', '/findings/dismissals')[0].body).toEqual({ key: KEY, reason: 'not_a_problem', note: '' })
    expect(await within(second).findByText(/Dismissed \(Not a problem\):/)).toBeInTheDocument()

    await user.click(within(second).getByRole('button', { name: `Bring back: ${DUPLICATE}` }))
    expect(await within(second).findByText(/Worth a look:/)).toBeInTheDocument()
  })

  it('says when a dismissed finding is back because the change or what it says changed', async () => {
    mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /assist/sessions': { items: [] },
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/validate': {
        ok: true,
        head_revision: 3,
        issues: [{ ordinal: 1, code: 'possible_duplicate', severity: 'advisory', message: DUPLICATE }],
      },
      'GET /mutations/mut_1/findings': { items: [finding({ came_back: true })] },
    })
    renderInProject(<ReviewDetail />, at)
    expect(await screen.findByText(/Back again: this change or what it says has changed/)).toBeInTheDocument()
  })
})
