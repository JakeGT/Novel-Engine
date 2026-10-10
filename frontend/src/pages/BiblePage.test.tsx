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
  it('opens on an index of its parts, and each part lists its entries', async () => {
    mockApi({ 'GET /entities': { items, limit: 200, offset: 0 }, 'GET /links': { items: [] } })
    const user = userEvent.setup()
    const { container } = renderInProject(<BiblePage />, { path: 'bible', at: 'bible' })

    // The first page is a way in, not a list of everything.
    const characters = await screen.findByRole('link', { name: 'Characters, 1 entry' })
    expect(characters).toHaveAttribute('href', '/projects/prj_1/bible?type=Character')
    // Each part names a few of what is inside, so it can be recognised without opening it.
    expect(characters).toHaveTextContent('Mara Venn')
    expect(screen.getByRole('link', { name: 'Locations, 1 entry' })).toHaveTextContent('Lantern Harbor')
    expect(screen.getByRole('link', { name: 'Everything, 2 entries' })).toHaveAttribute(
      'href',
      '/projects/prj_1/bible?view=all',
    )
    expect(screen.queryByRole('link', { name: /^Mara Venn/ })).not.toBeInTheDocument()
    await expectAccessible(container)

    await user.click(characters)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Characters')
    expect(screen.getByRole('link', { name: /^Mara Venn/ })).toHaveAttribute(
      'href',
      '/projects/prj_1/entries/ent_mara',
    )
    expect(screen.queryByRole('link', { name: /Lantern Harbor/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Old Captain/ })).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('Show archived'))
    expect(screen.getByRole('link', { name: /Old Captain/ })).toHaveTextContent('Archived')
  })

  it('shows everything by type, the most important first, a few at a time', async () => {
    const cast = [
      entity({ id: 'ent_bg', name: 'A Dockhand', aliases: [], attributes: { _importance: 'background' }, created_revision: 9 }),
      entity({ id: 'ent_unranked', name: 'Zed', aliases: [], attributes: {}, created_revision: 8 }),
      entity({
        id: 'ent_main',
        name: 'Mara Venn',
        aliases: [],
        attributes: { role: 'pilot', _importance: 'main', _tags: ['protagonist', 'harbor pilot'] },
        created_revision: 1,
      }),
      entity({ id: 'ent_prof', name: 'The Professor', aliases: [], attributes: { _tags: ['Mara’s mentor'], _importance: 'supporting', _needs_detail: true }, created_revision: 2 }),
      ...[1, 2, 3, 4].map((n) =>
        entity({ id: `ent_m${n}`, name: `Minor ${n}`, aliases: [], attributes: { _importance: 'minor' }, created_revision: 2 + n }),
      ),
      entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [], attributes: {} }),
    ]
    mockApi({ 'GET /entities': { items: cast, limit: 200, offset: 0 }, 'GET /links': { items: [] } })
    const user = userEvent.setup()
    const { container } = renderInProject(<BiblePage />, { path: 'bible', at: 'bible?view=all' })

    const section = await screen.findByRole('region', { name: /^Characters/ })
    const names = () =>
      [...section.querySelectorAll('.entry-name')].map((element) => element.textContent)
    // Main, then supporting, then minor (newest first): not alphabetical.
    expect(names()).toEqual(['Mara Venn', 'The Professor', 'Minor 4', 'Minor 3', 'Minor 2'])
    // Tags and standing are visible in the list, and a reserved name never is.
    const mara = within(section).getByRole('link', { name: /^Mara Venn/ })
    expect(mara).toHaveTextContent('Main')
    expect(mara).toHaveTextContent('protagonist')
    expect(within(section).getByRole('link', { name: /^The Professor/ })).toHaveTextContent('To fill in')
    expect(container).not.toHaveTextContent('_importance')
    expect(container).not.toHaveTextContent('_tags')
    expect(screen.getByRole('region', { name: /^Locations/ })).toBeInTheDocument()
    await expectAccessible(container)

    // The rest are one labelled click away, and unranked entries come before background ones.
    await user.click(within(section).getByRole('button', { name: 'Show 3 more characters' }))
    expect(names().slice(5)).toEqual(['Minor 1', 'Zed', 'A Dockhand'])
    expect(within(section).queryByRole('button', { name: /Show/ })).not.toBeInTheDocument()
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
