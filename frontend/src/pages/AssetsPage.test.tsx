import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Asset, AssetLifecycleEvent, AssetLink, AssetVersion } from '../api/client'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { AssetsPage } from './AssetsPage'

afterEach(() => vi.unstubAllGlobals())

const asset: Asset = {
  id: 'ast_chart',
  name: 'Harbor chart',
  lifecycle: 'active',
  created_revision: 0,
  retired_revision: null,
  created_at: '2026-10-09T10:00:00Z',
}
const version: AssetVersion = {
  id: 'asv_1',
  asset_id: 'ast_chart',
  version: 1,
  content_type: 'image/svg+xml',
  byte_length: 182,
  checksum: 'abc',
  source: null,
  licence: 'CC0-1.0',
  attribution: 'Novel Engine contributors',
  authority: 'author',
  visibility: 'project',
  created_revision: 0,
  retired_revision: null,
}
const link: AssetLink = {
  id: 'alk_1',
  asset_id: 'ast_chart',
  asset_version_id: 'asv_1',
  role: 'MAP_OF',
  target_record_type: 'entity',
  target_record_id: 'ent_harbor',
  created_revision: 7,
  retired_revision: null,
}
const events: AssetLifecycleEvent[] = [
  { action: 'asset_imported', asset_id: 'ast_chart', asset_version_id: null, actor_user_id: null, canon_revision: 0, created_at: '2026-10-09T10:00:00Z' },
  { action: 'version_retired', asset_id: 'ast_chart', asset_version_id: 'asv_1', actor_user_id: 'usr_owner', canon_revision: 7, created_at: '2026-10-09T11:00:00Z' },
]

describe('AssetsPage', () => {
  it('shows identity, versions, links, and the lifecycle audit without any upload control', async () => {
    mockApi({
      'GET /assets': { items: [asset] },
      'GET /assets/ast_chart/versions': { items: [version] },
      'GET /assets/ast_chart/lifecycle': { items: events },
      'GET /assets/ast_chart/links': { items: [link] },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: 'Harbor chart' }))

    expect(await screen.findByText(/licence CC0-1.0 · Novel Engine contributors/)).toBeInTheDocument()
    expect(screen.getByText('MAP_OF')).toBeInTheDocument()
    expect(screen.getByText(/version retired/)).toHaveTextContent('usr_owner · at r7')
    expect(screen.getByRole('button', { name: 'Harbor chart' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByText(/upload/i, { selector: 'button, input, label' })).not.toBeInTheDocument()
    expect(container.querySelector('input[type="file"]')).toBeNull()
    await expectAccessible(container)
  })
})
