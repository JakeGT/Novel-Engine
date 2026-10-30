import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { ArchiveEntry } from './ArchiveEntry'

afterEach(() => vi.unstubAllGlobals())

describe('ArchiveEntry', () => {
  it('needs an explicit acknowledgement before archiving', async () => {
    const api = mockApi({ 'POST /entities/ent_mara/retire': entity({ lifecycle: 'retired' }) })
    const user = userEvent.setup()
    const { container } = renderInProject(<ArchiveEntry entity={entity()} />)

    await user.click(screen.getByRole('button', { name: 'Archive this entry' }))
    const dialog = screen.getByRole('dialog', { name: 'Archive “Mara Venn”?' })
    expect(dialog).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Archive entry' })
    expect(confirm).toBeDisabled()
    await expectAccessible(container)

    await user.click(screen.getByLabelText('I understand this is a significant change'))
    await user.click(confirm)

    await waitFor(() => expect(api.find('POST', '/retire')).toHaveLength(1))
    expect(api.find('POST', '/retire')[0].body).toEqual({
      expected_revision: 3,
      acknowledge_high_impact: true,
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('can send the archive for review instead, and says where it went', async () => {
    const api = mockApi({
      'POST /entities/ent_mara/retire': problem(409, 'review_required', 'Review required.', {
        mutation_id: 'mut_7',
        reasons: 'destructive_operation',
      }),
    })
    const user = userEvent.setup()
    renderInProject(<ArchiveEntry entity={entity()} />)

    await user.click(screen.getByRole('button', { name: 'Archive this entry' }))
    await user.click(screen.getByRole('button', { name: 'Send for review instead' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('saved as a suggested change, not applied')
    expect(alert).toHaveTextContent('it archives something')
    expect(api.find('POST', '/retire')[0].body).toMatchObject({ acknowledge_high_impact: false })
    expect(screen.getByRole('link', { name: 'Review the change' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_7',
    )
  })

  it('closes with Escape and returns focus to the button that opened it', async () => {
    mockApi({})
    const user = userEvent.setup()
    renderInProject(<ArchiveEntry entity={entity()} />)

    const opener = screen.getByRole('button', { name: 'Archive this entry' })
    await user.click(opener)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })
})
