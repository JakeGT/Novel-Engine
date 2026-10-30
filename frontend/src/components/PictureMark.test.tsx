import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { NotePicture } from './PictureMark'

beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:picture') }))
})
afterEach(() => vi.unstubAllGlobals())

const anchor = (overrides: Record<string, unknown> = {}) => ({
  asset_id: 'ast_1',
  version_id: 'asv_1',
  label: '“Mara on the quay”, version 1',
  segment_id: 'seg_1',
  part_label: 'Her coat',
  region: [0.5, 0.2, 0.1, 0.1],
  ...overrides,
})

describe('NotePicture', () => {
  it('shows the picture a note was compared with, the part drawn on it, and a way to open it', async () => {
    const api = mockApi({ 'GET /content': new Blob(['x']) })
    const { container } = renderInProject(<NotePicture picture={anchor()} />)
    const picture = await screen.findByRole('img', { name: 'Mara on the quay: The part “Her coat” is marked.' })
    expect(picture).toHaveAttribute('src', 'blob:picture')
    const mark = container.querySelector('.picture-mark') as HTMLElement
    expect(mark.style.left).toBe('50%')
    expect(mark.style.width).toBe('10%')
    expect(screen.getByRole('link', { name: 'Open the picture' })).toHaveAttribute('href', '/projects/prj_1/pictures/ast_1')
    expect(api.find('GET', '/content')[0].path).toContain('/assets/ast_1/versions/asv_1/content')
    await expectAccessible(container)
  })

  it('says when a note is about the whole picture', async () => {
    mockApi({ 'GET /content': new Blob(['x']) })
    const { container } = renderInProject(
      <NotePicture picture={anchor({ segment_id: null, part_label: null, region: null })} />,
    )
    expect(await screen.findByText(/About the whole picture./)).toBeInTheDocument()
    expect(container.querySelector('.picture-mark')).toBeNull()
  })
})
