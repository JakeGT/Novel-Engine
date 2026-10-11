import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RevisionSummary } from '../api/client'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, VIEWER } from '../test/harness'
import { HistoryPage } from './HistoryPage'

afterEach(() => vi.unstubAllGlobals())

function revision(overrides: Partial<RevisionSummary>): RevisionSummary {
  return {
    revision: 1,
    commit_id: 'cmt_1',
    expected_head: 0,
    result_head: 1,
    schema_version_id: 'scv_1',
    operation_count: 1,
    created_at: '2026-10-09T10:00:00Z',
    origin: 'author',
    intent: 'edit',
    mutation_id: 'mut_1',
    actor_user_id: 'usr_me',
    decision_count: 0,
    evidence_count: 0,
    status: 'committed',
    ...overrides,
  }
}

const revisions = {
  items: [
    revision({ revision: 3, commit_id: 'cmt_3', origin: 'model', mutation_id: 'mut_3', operation_count: 2, decision_count: 1 }),
    revision({ revision: 2, commit_id: 'cmt_2', mutation_id: 'mut_2' }),
    revision({ revision: 1, commit_id: 'cmt_1' }),
  ],
}
const base = {
  'GET /revisions': revisions,
  'GET /entities': { items: [entity()], limit: 200, offset: 0 },
  'GET /tags': { items: [{ name: 'First draft done', revision: 2 }] },
}
const preview = {
  from_revision: 1,
  to_revision: 3,
  head_revision: 3,
  operations: [{ op: 'retire_entity', id: 'ent_mara' }],
  changes: [],
  conflicts: [],
  summary: 'revert r2..r3',
}

describe('HistoryPage', () => {
  it('lists versions newest first in plain words and marks the current one', async () => {
    mockApi(base)
    const { container } = renderInProject(<HistoryPage />, { context: { userId: 'usr_me' } })

    const rows = await screen.findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Version 3')
    expect(rows[0]).toHaveTextContent('Current')
    expect(rows[0]).toHaveTextContent('2 changes by the AI assistant · reviewed')
    expect(rows[1]).toHaveTextContent('1 change by you')
    expect(within(rows[0]).getByRole('link', { name: 'See what changed in version 3' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_3',
    )
    // There is nothing to go back to from the current version.
    expect(within(rows[0]).queryByRole('button', { name: /Go back/ })).not.toBeInTheDocument()
    expect(within(rows[0]).queryByRole('button', { name: /What changed since/ })).not.toBeInTheDocument()
    // A version an author has named carries its name.
    expect(rows[1]).toHaveTextContent('First draft done')
    await expectAccessible(container)
  })

  it('names a version and shows what has changed since one, in words', async () => {
    const api = mockApi({
      ...base,
      'POST /tags': { name: 'Before the rewrite', revision: 2 },
      'GET /revisions/3/diff/3': {
        items: [
          { record_type: 'entity', record_id: 'ent_new', revision: 3, operation_type: 'create_entity', commit_id: 'cmt_3', change: { type: 'Character', name: 'Tobin' }, created_at: '2026-10-09T10:00:00Z' },
        ],
      },
    })
    const user = userEvent.setup()
    renderInProject(<HistoryPage />, { context: { userId: 'usr_me' } })

    await user.click(await screen.findByRole('button', { name: 'Name version 2' }))
    const naming = screen.getByRole('dialog', { name: 'Name version 2' })
    expect(naming).toHaveTextContent('First draft done')
    await user.type(within(naming).getByLabelText('A name to find it by'), 'Before the rewrite')
    await user.click(within(naming).getByRole('button', { name: 'Name it' }))
    await waitFor(() => expect(api.find('POST', '/tags')[0].body).toEqual({ name: 'Before the rewrite', revision: 2 }))

    await user.click(screen.getByRole('button', { name: 'What changed since version 2' }))
    const since = await screen.findByRole('dialog', { name: 'What changed since version 2' })
    expect(await within(since).findByText('Add character “Tobin”')).toBeInTheDocument()
    expect(since).toHaveTextContent('1 change between version 2 and version 3')
  })

  it('previews going back, needs an acknowledgement, and restores as a new version', async () => {
    const api = mockApi({
      ...base,
      'POST /reverts/preview': preview,
      'POST /reverts': { mutation_id: 'mut_9', commit_id: 'cmt_9', revision: 4, already_applied: false },
    })
    const user = userEvent.setup()
    renderInProject(<HistoryPage />)

    await user.click(await screen.findByRole('button', { name: 'Go back to version 1' }))
    const dialog = screen.getByRole('dialog', { name: 'Go back to version 1?' })
    expect(await within(dialog).findByText('Going back would make 1 change.')).toBeInTheDocument()
    expect(within(dialog).getByText('Archive “Mara Venn”')).toBeInTheDocument()
    expect(api.find('POST', '/reverts/preview')[0].body).toEqual({ from_revision: 1, to_revision: null })

    const confirm = within(dialog).getByRole('button', { name: 'Go back to version 1' })
    expect(confirm).toBeDisabled()
    await user.click(within(dialog).getByLabelText(/I understand this changes a lot/))
    await user.click(confirm)

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.find('POST', '/reverts')[0].body).toEqual({
      from_revision: 1,
      to_revision: null,
      expected_revision: 3,
      acknowledge_high_impact: true,
    })
  })

  it('refuses to restore while there are conflicts, and says what they are', async () => {
    mockApi({
      ...base,
      'POST /reverts/preview': { ...preview, conflicts: ['Mara Venn was archived later'] },
    })
    const user = userEvent.setup()
    renderInProject(<HistoryPage />)

    await user.click(await screen.findByRole('button', { name: 'Go back to version 2' }))
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Mara Venn was archived later')
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Go back to version/ })).not.toBeInTheDocument()
  })

  it('keeps the dialog open and explains when the story changed underneath', async () => {
    mockApi({
      ...base,
      'POST /reverts/preview': preview,
      'POST /reverts': problem(409, 'revision_conflict', 'The branch head changed.'),
    })
    const user = userEvent.setup()
    renderInProject(<HistoryPage />)

    await user.click(await screen.findByRole('button', { name: 'Go back to version 1' }))
    const dialog = screen.getByRole('dialog')
    await user.click(await within(dialog).findByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Go back to version 1' }))
    expect(await within(dialog).findByText(/Someone else changed the story/)).toBeInTheDocument()
  })

  it('lets a reader look but not restore', async () => {
    mockApi(base)
    renderInProject(<HistoryPage />, { context: { access: VIEWER } })
    await screen.findByText('Version 3')
    expect(screen.queryByRole('button', { name: /Go back/ })).not.toBeInTheDocument()
  })
})
