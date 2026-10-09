import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RevertProposal, RevisionSummary } from '../api/client'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { RevisionsPage } from './RevisionsPage'

afterEach(() => vi.unstubAllGlobals())

const revision: RevisionSummary = {
  revision: 3,
  commit_id: 'cmt_3',
  expected_head: 2,
  result_head: 3,
  schema_version_id: 'scv_1',
  operation_count: 2,
  created_at: '2026-10-09T10:00:00Z',
  origin: 'model',
  intent: 'edit',
  mutation_id: 'mut_3',
  actor_user_id: null,
  decision_count: 1,
  evidence_count: 2,
  status: 'committed',
}

function preview(overrides: Partial<RevertProposal> = {}): RevertProposal {
  return {
    from_revision: 1,
    to_revision: 3,
    head_revision: 3,
    operations: [{ op: 'retire_entity', id: 'ent_new' }],
    changes: [
      { revision: 3, record_type: 'entity', record_id: 'ent_new', operation_type: 'create_entity', inverse_operation: 'retire_entity', reason: 'compensating' },
      { revision: 2, record_type: 'entity', record_id: 'ent_old', operation_type: 'retire_entity', inverse_operation: null, reason: 'a retired entity cannot be reactivated automatically' },
    ],
    conflicts: [],
    summary: 'revert r2..r3: 1 compensating operation(s), 1 manual, 0 conflict(s)',
    ...overrides,
  }
}

describe('RevisionsPage', () => {
  it('lists revisions and commits a revert only after the acknowledgement', async () => {
    const api = mockApi({
      'GET /revisions': { items: [revision] },
      'POST /reverts/preview': preview(),
      'POST /reverts': { commit_id: 'cmt_4', revision: 4, checksum: 'x', status: 'committed' },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<RevisionsPage />)

    expect(await screen.findByRole('link', { name: 'r3' })).toHaveAttribute(
      'href',
      '/projects/prj_1/mutations/mut_3',
    )
    await user.type(screen.getByLabelText('Return canon to revision'), '1')
    await user.click(screen.getByRole('button', { name: 'Preview revert' }))

    expect(await screen.findByText(/1 compensating operation/)).toBeInTheDocument()
    expect(screen.getByText(/a retired entity cannot be reactivated automatically/)).toBeInTheDocument()
    const commit = screen.getByRole('button', { name: 'Commit revert' })
    expect(commit).toBeDisabled()
    await expectAccessible(container)

    await user.click(screen.getByLabelText('I understand this is a high-impact change to canon'))
    await user.click(commit)
    await waitFor(() =>
      expect(api.find('POST', '/reverts')[0].body).toEqual({
        from_revision: 1,
        to_revision: null,
        expected_revision: 3,
        acknowledge_high_impact: true,
      }),
    )
  })

  it('refuses to offer a commit while the preview reports conflicts', async () => {
    mockApi({
      'GET /revisions': { items: [revision] },
      'POST /reverts/preview': preview({
        conflicts: ['entity ent_harbor is still referenced by fact fct_at'],
      }),
    })
    const user = userEvent.setup()
    renderInProject(<RevisionsPage />)

    await user.type(await screen.findByLabelText('Return canon to revision'), '1')
    await user.click(screen.getByRole('button', { name: 'Preview revert' }))

    expect(await screen.findByText('entity ent_harbor is still referenced by fact fct_at')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Commit revert' })).not.toBeInTheDocument()
  })
})
