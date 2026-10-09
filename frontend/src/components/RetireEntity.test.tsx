import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { RetireEntity } from './RetireEntity'

afterEach(() => vi.unstubAllGlobals())

describe('RetireEntity', () => {
  it('requires an explicit acknowledgement and sends it with the request', async () => {
    const api = mockApi({
      'POST /entities/ent_mara/retire': entity({ lifecycle: 'retired', retired_revision: 4 }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<RetireEntity entity={entity()} />)

    await user.click(screen.getByRole('button', { name: 'Retire…' }))
    const confirm = screen.getByRole('button', { name: 'Retire entity' })
    expect(confirm).toBeDisabled()
    await expectAccessible(container)

    await user.click(screen.getByLabelText('I understand this is a high-impact change'))
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    await waitFor(() => expect(api.find('POST', '/retire')).toHaveLength(1))
    expect(api.find('POST', '/retire')[0].body).toEqual({
      expected_revision: 3,
      acknowledge_high_impact: true,
    })
  })

  it('shows a parked proposal instead of claiming the entity was retired', async () => {
    const api = mockApi({
      'POST /entities/ent_mara/retire': problem(
        409,
        'review_required',
        'this proposal requires an explicit human approval (destructive_operation)',
        { mutation_id: 'mut_42', reasons: 'destructive_operation' },
      ),
    })
    const user = userEvent.setup()
    renderInProject(<RetireEntity entity={entity()} />)

    await user.click(screen.getByRole('button', { name: 'Retire…' }))
    await user.click(screen.getByRole('button', { name: 'Send for review instead' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('needs review before it can become canon')
    expect(alert).toHaveTextContent('not committed')
    expect(screen.getByRole('link', { name: 'Open the proposal for review' })).toHaveAttribute(
      'href',
      '/projects/prj_1/mutations/mut_42',
    )
    expect(api.find('POST', '/retire')[0].body).toMatchObject({ acknowledge_high_impact: false })
  })
})
