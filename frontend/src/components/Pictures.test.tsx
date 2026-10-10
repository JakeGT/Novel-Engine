import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AssetLink } from '../api/client'
import { mainPicture } from '../lib/pictures'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply, VIEWER } from '../test/harness'
import { PicturesPanel } from './Pictures'

function link(overrides: Partial<AssetLink> = {}): AssetLink {
  return {
    id: 'lnk_1',
    asset_id: 'ast_1',
    asset_version_id: 'asv_1',
    role: 'DEPICTS',
    target_record_type: 'entity',
    target_record_id: 'ent_mara',
    created_revision: 2,
    retired_revision: null,
    ...overrides,
  }
}

const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'Mara at the helm.png', { type: 'image/png' })

beforeEach(() => {
  let n = 0
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:picture-${(n += 1)}`) }))
})
afterEach(() => vi.unstubAllGlobals())

describe('PicturesPanel', () => {
  it('shows an entry’s pictures, newest first, with the newest as the main one', async () => {
    mockApi({
      'GET /links': {
        items: [link(), link({ id: 'lnk_2', asset_id: 'ast_2', asset_version_id: 'asv_2', created_revision: 5 })],
      },
      'GET /content': new Blob(['x']),
    })
    const { container } = renderInProject(<PicturesPanel entity={entity()} />)

    const first = await screen.findByRole('img', { name: 'Picture 1 of Mara Venn' })
    expect(first).toHaveAttribute('src', expect.stringMatching(/^blob:picture-/))
    expect(await screen.findByRole('img', { name: 'Picture 2 of Mara Venn' })).toBeInTheDocument()
    expect(screen.getByText('Main')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('uploads a chosen image against the current version and reports a clash', async () => {
    let attempts = 0
    const api = mockApi({
      'GET /links': { items: [] },
      'POST /pictures': () => {
        attempts += 1
        return attempts === 1
          ? problem(409, 'revision_conflict', 'The branch head changed.')
          : reply(201, { link: link(), version: { id: 'asv_1' } })
      },
    })
    const user = userEvent.setup()
    renderInProject(<PicturesPanel entity={entity()} />)

    expect(await screen.findByText(/No pictures yet/)).toBeInTheDocument()
    await user.upload(screen.getByLabelText('Choose a picture of Mara Venn'), png())

    expect(await screen.findByText(/Someone else changed the story/)).toBeInTheDocument()
    const first = api.find('POST', '/pictures')[0]
    expect(first.search.get('expected_revision')).toBe('3')
    expect(first.search.get('name')).toBe('Mara at the helm')

    await user.click(screen.getByRole('button', { name: 'Save on top of the latest version' }))
    await waitFor(() => expect(api.find('POST', '/pictures')).toHaveLength(2))
    expect(api.find('POST', '/pictures')[1].search.get('expected_revision')).toBe('5')
  })

  it('refuses files that are not pictures before sending anything', async () => {
    const api = mockApi({ 'GET /links': { items: [] } })
    const user = userEvent.setup({ applyAccept: false })
    renderInProject(<PicturesPanel entity={entity()} />)

    await screen.findByText(/No pictures yet/)
    await user.upload(
      screen.getByLabelText('Choose a picture of Mara Venn'),
      new File(['<svg/>'], 'drawing.svg', { type: 'image/svg+xml' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PNG, JPEG and WebP')
    expect(api.find('POST', '/pictures')).toHaveLength(0)
  })

  it('removes a picture by retiring its link', async () => {
    const api = mockApi({
      'GET /links': { items: [link()] },
      'GET /content': new Blob(['x']),
      'POST /links/lnk_1/retire': {},
      [`POST /assets/${link().asset_id}/links`]: reply(201, link({ id: 'lnk_2' })),
    })
    const user = userEvent.setup()
    renderInProject(<PicturesPanel entity={entity()} />)

    await user.click(await screen.findByRole('button', { name: 'Remove picture 1 of Mara Venn' }))
    await waitFor(() => expect(api.find('POST', '/retire')).toHaveLength(1))
    expect(api.find('POST', '/retire')[0].body).toEqual({ expected_revision: 3 })

    // Removal is not confirmed beforehand; it is put right afterwards.
    await user.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.find('POST', `/assets/${link().asset_id}/links`)).toHaveLength(1))
    expect(api.find('POST', `/assets/${link().asset_id}/links`)[0].body).toMatchObject({
      target_id: link().target_record_id,
      asset_version_id: link().asset_version_id,
      role: link().role,
    })
  })

  it('shows pictures to a reader without any way to change them, and nothing when there are none', async () => {
    mockApi({ 'GET /links': { items: [link()] }, 'GET /content': new Blob(['x']) })
    const first = renderInProject(<PicturesPanel entity={entity()} />, { context: { access: VIEWER } })
    await screen.findByRole('img', { name: 'Picture 1 of Mara Venn' })
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    first.unmount()

    mockApi({ 'GET /links': { items: [] } })
    const { container } = renderInProject(<PicturesPanel entity={entity()} />, { context: { access: VIEWER } })
    await waitFor(() => expect(container.querySelector('section')).toBeNull())
  })

  it('treats only current picture links as pictures', () => {
    const older = link({ created_revision: 1 })
    const newer = link({ id: 'lnk_2', created_revision: 4 })
    const retired = link({ id: 'lnk_3', created_revision: 9, retired_revision: 10 })
    const evidence = link({ id: 'lnk_4', role: 'EVIDENCE_FOR', created_revision: 8 })
    expect(mainPicture([older, retired, evidence, newer])?.id).toBe('lnk_2')
    expect(mainPicture([retired, evidence])).toBeUndefined()
  })
})
