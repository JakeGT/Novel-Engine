import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { MediaPage } from './MediaPage'

afterEach(() => vi.unstubAllGlobals())

const asset = { id: 'ast_1', name: 'Harbor map', lifecycle: 'active', created_revision: 2, retired_revision: null, created_at: '2026-10-09T10:00:00Z' }

describe('MediaPage', () => {
  it('is honest that it lists media rather than uploading it', async () => {
    mockApi({ 'GET /assets': { items: [] }, 'GET /entities': { items: [], limit: 200, offset: 0 } })
    renderInProject(<MediaPage />)
    expect(await screen.findByText('No media yet')).toBeInTheDocument()
    expect(screen.getByText(/uploading files from here is not available yet/)).toBeInTheDocument()
  })

  it('opens an item to show its versions and what it is linked to by name', async () => {
    mockApi({
      'GET /assets': { items: [asset] },
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /assets/ast_1/versions': {
        items: [{ id: 'asv_1', asset_id: 'ast_1', version: 1, content_type: 'image/png', byte_length: 2048, checksum: 'x', source: null, licence: 'CC-BY', attribution: null, authority: null, visibility: null, created_revision: 2, retired_revision: null }],
      },
      'GET /assets/ast_1/links': {
        items: [{ id: 'lnk_1', asset_id: 'ast_1', asset_version_id: null, role: 'reference', target_record_type: 'entity', target_record_id: 'ent_mara', created_revision: 2, retired_revision: null }],
      },
      'GET /assets/ast_1/lifecycle': { items: [] },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<MediaPage />)

    const toggle = await screen.findByRole('button', { name: /Harbor map/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    const item = toggle.closest('li') as HTMLElement
    expect(await within(item).findByText(/image\/png · 2.0 KB/)).toBeInTheDocument()
    expect(within(item).getByText('Licence: CC-BY')).toBeInTheDocument()
    expect(await within(item).findByText('Mara Venn')).toBeInTheDocument()
    await expectAccessible(container)
  })
})
