import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { FromText } from './FromText'

afterEach(() => vi.unstubAllGlobals())

const passage = {
  source_id: 'src_1',
  title: 'The lamp room at night',
  version_number: 2,
  before: 'Ines climbed to the lamp room. ',
  quote: 'Tobin Ashe waited by the glass.',
  after: ' The sea was quiet.',
}

describe('FromText', () => {
  it('quotes the words a change came from and, for a scene, opens the studio at them', async () => {
    const api = mockApi({ 'GET /texts/passage': { ...passage, scene_id: 'pln_s' } })
    const { container } = renderInProject(
      <FromText evidence={[{ source_artefact_id: 'srv_2', location: 'chars:31-62' }]} />,
    )
    expect(await screen.findByText('Tobin Ashe waited by the glass.')).toBeInTheDocument()
    expect(api.find('GET', '/texts/passage')[0].search.get('version_id')).toBe('srv_2')
    expect(screen.getByRole('link', { name: 'The lamp room at night' })).toHaveAttribute(
      'href',
      '/projects/prj_1/texts/src_1#at-31-62',
    )
    expect(screen.getByRole('link', { name: 'See it in the scene' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan/pln_s/write?at=31-62&version=srv_2',
    )
    await expectAccessible(container)
  })

  it('opens the scene without a passage when the change gives no place in the text', async () => {
    mockApi({ 'GET /texts/passage': { ...passage, before: '', quote: '', after: '', scene_id: 'pln_s' } })
    renderInProject(<FromText evidence={[{ source_artefact_id: 'srv_2', location: null }]} />)
    const link = await screen.findByRole('link', { name: 'See it in the scene' })
    expect(link).toHaveAttribute('href', '/projects/prj_1/plan/pln_s/write')
    expect(link.closest('p')).toHaveTextContent('From The lamp room at night. See it in the scene')
  })

  it('offers no scene for words from an ordinary text', async () => {
    mockApi({ 'GET /texts/passage': { ...passage, scene_id: null } })
    renderInProject(<FromText evidence={[{ source_artefact_id: 'srv_2', location: 'chars:31-62' }]} />)
    expect(await screen.findByText('Tobin Ashe waited by the glass.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'See it in the scene' })).not.toBeInTheDocument()
  })
})
