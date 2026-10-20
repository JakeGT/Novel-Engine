import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { MediaPage } from './MediaPage'

afterEach(() => vi.unstubAllGlobals())

const asset = { id: 'ast_1', name: 'Harbor map', lifecycle: 'active', created_revision: 2, retired_revision: null, created_at: '2026-10-09T10:00:00Z' }

describe('MediaPage', () => {
  it('is honest that it lists media rather than uploading it', async () => {
    mockApi({ 'GET /assets': { items: [] }, 'GET /entities': { items: [], limit: 200, offset: 0 } })
    renderInProject(<MediaPage />)
    expect(await screen.findByText('No media yet')).toBeInTheDocument()
    expect(screen.getByText(/adding files from this page is not\s+available yet/)).toBeInTheDocument()
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

describe('MediaPage pictures', () => {
  const version = {
    id: 'asv_1', asset_id: 'ast_1', version: 1, content_type: 'image/png', byte_length: 2048, checksum: 'x', source: null,
    licence: null, attribution: null, authority: null, visibility: null, created_revision: 2, retired_revision: null,
  }
  const libraryPicture = (overrides: Record<string, unknown> = {}) => ({
    asset_id: 'ast_1', version_id: 'asv_1', name: 'Harbor map', content_type: 'image/png', byte_length: 2048,
    created_at: '2026-10-09T10:00:00Z', shown: 1, processing: 'ready', processing_message: null, has_thumbnail: true,
    ...overrides,
  })
  const routes = (picture: Record<string, unknown>, processing: Record<string, unknown>) => ({
    'GET /assets': { items: [asset] },
    'GET /entities': { items: [entity()], limit: 200, offset: 0 },
    'GET /pictures': { items: [picture], storage: { used_bytes: 950 * 1024 * 1024, quota_bytes: 1024 * 1024 * 1024 } },
    'GET /content': new Blob(['x']),
    'GET /assets/ast_1/versions': { items: [version] },
    'GET /assets/ast_1/links': { items: [] },
    'GET /assets/ast_1/lifecycle': { items: [] },
    'GET /versions/asv_1/processing': processing,
  })

  it('shows each picture small in the list and how much room pictures take', async () => {
    let n = 0
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:picture-${(n += 1)}`) }))
    const api = mockApi(routes(libraryPicture(), { status: 'ready', retryable: false, width: 640, height: 480, copies: ['thumbnail', 'web'] }))
    const user = userEvent.setup()
    const { container } = renderInProject(<MediaPage />)
    expect(await screen.findByText(/use 950.0 MB of 1.0 GB/)).toBeInTheDocument()
    expect(screen.getByText(/Remove pictures you no longer need/)).toBeInTheDocument()
    await waitFor(() => expect(api.find('GET', '/content').map((call) => call.search.get('size'))).toContain('thumbnail'))

    await user.click(await screen.findByRole('button', { name: /Harbor map/ }))
    expect(await screen.findByText(/640 × 480 pixels/)).toBeInTheDocument()
    await waitFor(() => expect(api.find('GET', '/content').map((call) => call.search.get('size'))).toContain('web'))
    await expectAccessible(container)
  })

  it('says plainly when a picture could not be prepared, and tries again', async () => {
    let n = 0
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:picture-${(n += 1)}`) }))
    const failed = {
      status: 'failed', failure_code: 'took_too_long', message: 'Opening this picture took too long. Try again later.',
      retryable: true, helps: true, attempts: 1, copies: [],
    }
    const api = mockApi({
      ...routes(libraryPicture({ processing: 'failed', has_thumbnail: false, processing_message: failed.message }), failed),
      'POST /processing/retry': { ...failed, status: 'queued', failure_code: null, message: null, retryable: false },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<MediaPage />)
    expect(await screen.findByText('Not ready')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Harbor map/ }))
    expect(await screen.findByText('No small copy for lists.')).toBeInTheDocument()
    expect(screen.getByText(/took too long/)).toBeInTheDocument()
    expect(screen.getByText(/The file you added is kept as it is/)).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(api.find('POST', '/processing/retry')).toHaveLength(1))
    expect(await screen.findByText(/Waiting to be prepared/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('offers no Try again to a reader', async () => {
    const failed = { status: 'failed', failure_code: 'damaged', message: 'This picture could not be read.', retryable: true, copies: [] }
    mockApi(routes(libraryPicture({ processing: 'failed', has_thumbnail: false }), failed))
    const user = userEvent.setup()
    renderInProject(<MediaPage />, { context: { access: VIEWER } })
    await user.click(await screen.findByRole('button', { name: /Harbor map/ }))
    expect(await screen.findByText('No small copy for lists.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })
})
