import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { EntityList } from './EntityList'

afterEach(() => vi.unstubAllGlobals())

describe('EntityList', () => {
  it('lists project-scoped entities and filters by type, search, and lifecycle', async () => {
    const api = mockApi({
      'GET /schemas/vocabulary': VOCABULARY,
      'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
      'GET /entities': {
        items: [entity(), entity({ id: 'ent_old', name: 'Old Light', type: 'Location', lifecycle: 'retired', aliases: [] })],
        limit: 100,
        offset: 0,
      },
      'GET /entities/search': { items: [entity()], limit: 100, offset: 0 },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<EntityList />)

    const link = await screen.findByRole('link', { name: 'Mara Venn' })
    // Characters open in the character view; everything else in the entity view.
    expect(link).toHaveAttribute('href', '/projects/prj_1/characters/ent_mara')
    expect(screen.getByRole('link', { name: 'Old Light' })).toHaveAttribute(
      'href',
      '/projects/prj_1/entities/ent_old',
    )
    expect(screen.getByText('also The Fox')).toBeInTheDocument()
    expect(screen.getByText('Retired')).toBeInTheDocument()
    expect(api.calls[0].path).toContain('/api/v1/projects/prj_1/')
    await expectAccessible(container)

    await screen.findAllByRole('option', { name: 'Location' })
    await user.selectOptions(screen.getByLabelText('Type', { selector: '.filters select' }), 'Location')
    await user.click(screen.getByLabelText('Include retired'))
    await waitFor(() => {
      const last = api.find('GET', '/entities').at(-1)
      expect(last?.search.get('entity_type')).toBe('Location')
      expect(last?.search.get('include_retired')).toBe('true')
    })

    await user.type(screen.getByLabelText('Search'), 'fox')
    await waitFor(() => {
      const last = api.find('GET', '/entities/search').at(-1)
      expect(last?.search.get('q')).toBe('fox')
      expect(last?.search.get('entity_type')).toBe('Location')
    })
  })
})
