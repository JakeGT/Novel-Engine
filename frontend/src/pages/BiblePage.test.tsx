import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { BiblePage } from './BiblePage'

afterEach(() => vi.unstubAllGlobals())

const items = [
  entity(),
  entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [], attributes: {} }),
  entity({ id: 'ent_old', name: 'Old Captain', aliases: [], lifecycle: 'retired', retired_revision: 4 }),
]

describe('BiblePage', () => {
  it('lists active entries, filters by type, and hides archived ones until asked', async () => {
    mockApi({ 'GET /entities': { items, limit: 200, offset: 0 }, 'GET /links': { items: [] } })
    const user = userEvent.setup()
    const { container } = renderInProject(<BiblePage />, { path: 'bible', at: 'bible' })

    expect(await screen.findByRole('link', { name: /Mara Venn/ })).toHaveAttribute(
      'href',
      '/projects/prj_1/entries/ent_mara',
    )
    expect(screen.getByRole('link', { name: /Lantern Harbor/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Old Captain/ })).not.toBeInTheDocument()
    expect(screen.getByText('2 entries')).toBeInTheDocument()
    await expectAccessible(container)

    const filters = screen.getByRole('group', { name: 'Filter by type' })
    await user.click(within(filters).getByRole('button', { name: /Locations/ }))
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Locations')
    expect(screen.queryByRole('link', { name: /Mara Venn/ })).not.toBeInTheDocument()

    await user.click(within(filters).getByRole('button', { name: /Everything/ }))
    await user.click(screen.getByLabelText('Show archived'))
    expect(screen.getByRole('link', { name: /Old Captain/ })).toHaveTextContent('Archived')
  })

  it('searches on the server so nicknames and unloaded entries are found', async () => {
    const api = mockApi({
      'GET /entities': { items, limit: 200, offset: 0 },
      'GET /links': { items: [] },
      'GET /entities/search': { items: [items[0]], limit: 100, offset: 0 },
    })
    const user = userEvent.setup()
    renderInProject(<BiblePage />, { path: 'bible', at: 'bible' })

    await user.type(await screen.findByLabelText('Search entries'), 'fox')
    expect(await screen.findByText('1 match for “fox”')).toBeInTheDocument()
    expect(api.find('GET', '/entities/search').at(-1)?.search.get('q')).toBe('fox')
  })

  it('invites the first entry on an empty project, and offers nothing to a reader', async () => {
    mockApi({ 'GET /entities': { items: [], limit: 200, offset: 0 }, 'GET /links': { items: [] } })
    const newEntry = vi.fn()
    const user = userEvent.setup()
    const first = renderInProject(<BiblePage />, { path: 'bible', at: 'bible', context: { newEntry } })

    await user.click(await screen.findByRole('button', { name: 'Add your first entry' }))
    expect(newEntry).toHaveBeenCalled()
    first.unmount()

    renderInProject(<BiblePage />, { path: 'bible', at: 'bible', context: { access: VIEWER } })
    expect(await screen.findByText('Your story bible is empty')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /entry/i })).not.toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('You can read this project but not change it')
  })
})
