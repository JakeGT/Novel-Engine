import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MutationDetail as Detail } from '../api/client'
import { mutation, operation } from '../test/fixtures'
import { expectAccessible, mockApi, type RecordedCall, problem, renderInProject, VIEWER } from '../test/harness'
import { MutationDetail } from './MutationDetail'

afterEach(() => vi.unstubAllGlobals())

const page = { path: 'mutations/:mutationId', at: 'mutations/mut_1' }

describe('MutationDetail', () => {
  it('reviews per operation, approves, and only shows canon after the commit succeeds', async () => {
    let current: Detail = mutation()
    const api = mockApi({
      'GET /mutations/mut_1': () => current,
      'POST /mutations/mut_1/operations/1/decision': (call: RecordedCall) => {
        const body = call.body as { decision: string; note: string | null }
        current = mutation({
          operations: [
            current.operations[0],
            {
              ...current.operations[1],
              decision: {
                decision: body.decision,
                operation_revision: 1,
                actor_user_id: 'usr_1',
                note: body.note,
                created_at: '2026-10-09T10:05:00Z',
              },
            },
          ],
        })
        return current
      },
      'POST /mutations/mut_1/approve': () => {
        current = { ...current, status: 'approved' }
        return current
      },
      'POST /mutations/mut_1/commit': () => {
        current = { ...current, status: 'committed', committed_revision: 4 }
        return { commit_id: 'cmt_9', revision: 4, checksum: 'abc', status: 'committed' }
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<MutationDetail />, page)

    expect(await screen.findByText('Proposed · awaiting review', { selector: 'h2 .badge' })).toBeInTheDocument()
    expect(screen.getByText(/Not canon yet\. 2 of 2 operations would be committed/)).toBeInTheDocument()
    expect(screen.getByText(/because it did not come directly from an author/)).toBeInTheDocument()
    // The diff shows current canon next to the proposed change.
    expect(screen.getByText('Proposed change (changes name)')).toBeInTheDocument()
    // It cannot be committed before it is approved.
    expect(screen.getByRole('button', { name: 'Commit to canon' })).toBeDisabled()
    await expectAccessible(container)

    await user.type(screen.getByLabelText('Note for operation 2 (optional)'), 'wrong surname')
    await user.click(screen.getByRole('button', { name: 'Reject operation 2' }))
    expect(await screen.findByText('Rejected · will not be committed')).toBeInTheDocument()
    expect(api.find('POST', '/operations/1/decision')[0].body).toEqual({
      decision: 'rejected',
      note: 'wrong surname',
    })
    expect(screen.getByText(/1 of 2 operations would be committed/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText('Approved · not yet canon', { selector: 'h2 .badge' })).toBeInTheDocument()
    expect(screen.queryByText(/Committed to canon/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Commit to canon' }))
    expect(await screen.findByText('Committed to canon as revision r4.')).toBeInTheDocument()
    expect(api.find('POST', '/commit')).toHaveLength(1)
    // A committed proposal offers no further actions.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Reject proposal' })).not.toBeInTheDocument(),
    )
  })

  it('does not claim canon when the commit is refused', async () => {
    mockApi({
      'GET /mutations/mut_1': mutation({ status: 'approved' }),
      'POST /mutations/mut_1/commit': problem(422, 'validation_error', '[cardinality_conflict] already asserted'),
    })
    const user = userEvent.setup()
    renderInProject(<MutationDetail />, page)

    await user.click(await screen.findByRole('button', { name: 'Commit to canon' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Not committed: [cardinality_conflict]')
    expect(screen.queryByText(/Committed to canon/)).not.toBeInTheDocument()
    expect(screen.getByText('Approved · not yet canon', { selector: 'h2 .badge' })).toBeInTheDocument()
  })

  it('edits an operation as a new revision and keeps the text if the edit fails', async () => {
    let attempts = 0
    const api = mockApi({
      'GET /mutations/mut_1': mutation(),
      'PUT /mutations/mut_1/operations/0': () => {
        attempts += 1
        return attempts === 1
          ? problem(422, 'validation_error', 'name is required')
          : mutation({ operations: [operation({ revision: 2 }), mutation().operations[1]] })
      },
    })
    const user = userEvent.setup()
    renderInProject(<MutationDetail />, page)

    await user.click(await screen.findByRole('button', { name: 'Edit operation 1' }))
    const editor = screen.getByLabelText('Operation 1 (JSON)')
    await user.clear(editor)
    await user.click(editor)
    await user.paste('{"op":"create_entity","id":"tmp:a","type":"Character","name":"Iven Marsh"}')
    await user.type(screen.getByLabelText('Reason for the edit (optional)'), 'full name')
    await user.click(screen.getByRole('button', { name: 'Save as new revision' }))

    expect(await screen.findByText('name is required')).toBeInTheDocument()
    expect(screen.getByLabelText('Operation 1 (JSON)')).toHaveValue(
      '{"op":"create_entity","id":"tmp:a","type":"Character","name":"Iven Marsh"}',
    )

    await user.click(screen.getByRole('button', { name: 'Save as new revision' }))
    await waitFor(() => expect(api.find('PUT', '/operations/0')).toHaveLength(2))
    expect(api.find('PUT', '/operations/0')[1].body).toEqual({
      operation: { op: 'create_entity', id: 'tmp:a', type: 'Character', name: 'Iven Marsh' },
      reason: 'full name',
    })
  })

  it('separates blocking problems from advisories in the validation preview', async () => {
    mockApi({
      'GET /mutations/mut_1': mutation(),
      'POST /mutations/mut_1/validate': {
        ok: false,
        issues: [
          { ordinal: 0, code: 'name_collision', message: 'another active Character is named Iven', severity: 'advisory' },
          { ordinal: 1, code: 'retired_reference', message: 'entity ent_mara is retired', severity: 'blocking' },
        ],
      },
    })
    const user = userEvent.setup()
    renderInProject(<MutationDetail />, page)

    await user.click(await screen.findByRole('button', { name: 'Check against canon' }))

    expect(await screen.findByText('There are problems that would block a commit.')).toBeInTheDocument()
    const [first, second] = screen.getAllByRole('listitem').filter((item) => item.classList.contains('operation'))
    expect(within(first).getByRole('note')).toHaveTextContent('Advisory: another active Character')
    expect(within(second).getByRole('alert')).toHaveTextContent('Blocks commit: entity ent_mara is retired')
  })

  it('offers retargeting for a stale proposal and blocks its commit', async () => {
    const api = mockApi({
      'GET /mutations/mut_1': mutation({ status: 'approved', is_stale: true, head_revision: 6 }),
      'POST /mutations/mut_1/retarget': mutation({ expected_revision: 6, head_revision: 6 }),
    })
    const user = userEvent.setup()
    renderInProject(<MutationDetail />, page)

    expect(await screen.findByText('This proposal is out of date.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Commit to canon' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Retarget to r6' }))
    await waitFor(() =>
      expect(api.find('POST', '/retarget')[0].body).toEqual({ expected_revision: 6 }),
    )
  })

  it('lets a read-only member inspect but not act', async () => {
    mockApi({ 'GET /mutations/mut_1': mutation() })
    const { container } = renderInProject(<MutationDetail />, { ...page, context: { access: VIEWER } })

    expect(await screen.findByText(/You have read-only access/)).toBeInTheDocument()
    expect(screen.getByText('Operation 1: create_entity', { exact: false })).toBeInTheDocument()
    for (const name of [/Accept operation/, /Reject operation/, /Edit operation/, 'Approve', 'Commit to canon', 'Reject proposal']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    await expectAccessible(container)
  })

  it('tells an author arriving from the guided interview that nothing is canon yet', async () => {
    mockApi({ 'GET /mutations/mut_1': mutation() })
    renderInProject(<MutationDetail />, {
      ...page,
      state: { fromGuidedWorkflow: true, discarded: [{ kind: 'fact', reason: "unsupported predicate 'ADORES'" }] },
    })

    expect(await screen.findByText('Nothing has been saved to canon.')).toBeInTheDocument()
    expect(screen.getByText("fact: unsupported predicate 'ADORES'")).toBeInTheDocument()
  })
})
