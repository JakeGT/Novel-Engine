import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReaderGuide, RevisionSummary } from '../api/client'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { guideFileName } from '../lib/guide'
import { ReaderGuidePage } from './ReaderGuidePage'

afterEach(() => vi.unstubAllGlobals())

const route = { path: 'plan/:planId/guide', at: 'plan/pln_s/guide' }

function guide(overrides: Partial<ReaderGuide> = {}): ReaderGuide {
  const version = overrides.version ?? 3
  return {
    title: `The story so far, after “The Docks” — version ${version}`,
    markdown: '---\ntitle: "x"\nversion: 3\n---\n\n# The story so far\n',
    story_line: 'the story itself',
    version,
    scene_id: 'pln_s',
    scene_title: 'The Docks',
    scene_number: 1,
    scene_count: 2,
    viewpoint_id: null,
    viewpoint_name: null,
    bound_to: `The story itself, as it stood at version ${version}; up to scene 1 of 2, “The Docks”; as the reader knows it.`,
    leaves_out: 'Leaves out secrets not yet shown to the reader, anything dated after this scene, and anyone who has not yet appeared.',
    sections: [
      { heading: 'People', entries: [{ name: 'Mara Venn', lines: ['Mara Venn is located at Lantern Harbor.'] }] },
      { heading: 'Places', entries: [{ name: 'Lantern Harbor', lines: [] }] },
    ],
    rules: 1,
    ...overrides,
  }
}

function revision(number: number): RevisionSummary {
  return {
    revision: number,
    commit_id: `cmt_${number}`,
    expected_head: number - 1,
    result_head: number,
    schema_version_id: 'scv_1',
    operation_count: 1,
    created_at: '2026-10-09T10:00:00Z',
    origin: 'author',
    intent: 'edit',
    mutation_id: `mut_${number}`,
    actor_user_id: 'usr_me',
    decision_count: 0,
    evidence_count: 0,
    status: 'committed',
  }
}

const base = {
  'GET /entities': { items: [entity(), entity({ id: 'ent_tobin', name: 'Tobin Ashe', slug: 'tobin' })], limit: 200, offset: 0 },
  'GET /revisions': { items: [revision(3), revision(2), revision(1)] },
  'GET /tags': { items: [{ name: 'First draft done', revision: 2 }] },
}

describe('ReaderGuidePage', () => {
  it('shows the guide bound to its version, scene and viewpoint, with what the reader knows', async () => {
    mockApi({ ...base, 'GET /reader/guide': guide() })
    const { container } = renderInProject(<ReaderGuidePage />, route)

    const sheet = await screen.findByRole('article', { name: /The story so far, after “The Docks” — version 3/ })
    expect(within(sheet).getByText(/as it stood at version 3; up to scene 1 of 2/)).toBeInTheDocument()
    expect(within(sheet).getByRole('heading', { name: 'People' })).toBeInTheDocument()
    expect(within(sheet).getByText('Mara Venn is located at Lantern Harbor.')).toBeInTheDocument()
    expect(within(sheet).getByText('Nothing more is known yet.')).toBeInTheDocument()
    // Sharing is by print or file; there is no public link, and the page says why.
    expect(screen.getByText(/no link to share this guide/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('asks for the guide at an earlier version, or as a character knows it', async () => {
    const api = mockApi({
      ...base,
      'GET /reader/guide': (call: { search: URLSearchParams }) =>
        guide({ version: Number(call.search.get('version') ?? 3) }),
    })
    renderInProject(<ReaderGuidePage />, route)
    await screen.findByRole('article')

    const versions = screen.getByLabelText('Version of the story')
    // Earlier versions are offered by number, with any name the author gave them.
    expect(within(versions).getByRole('option', { name: /Version 2 · First draft done/ })).toBeInTheDocument()
    expect(within(versions).queryByRole('option', { name: /^Version 3/ })).not.toBeInTheDocument()
    await userEvent.selectOptions(versions, '2')
    await screen.findByRole('article', { name: /version 2/ })
    expect(api.find('GET', '/reader/guide').at(-1)?.search.get('version')).toBe('2')

    await userEvent.selectOptions(screen.getByLabelText('Whose guide'), 'ent_tobin')
    await waitFor(() => expect(api.find('GET', '/reader/guide').at(-1)?.search.get('viewpoint')).toBe('ent_tobin'))
  })

  it('prints the page and downloads the Markdown file', async () => {
    mockApi({ ...base, 'GET /reader/guide': guide() })
    const print = vi.fn()
    vi.stubGlobal('print', print)
    const created: Blob[] = []
    const revoked: string[] = []
    vi.stubGlobal(
      'URL',
      Object.assign(URL, {
        createObjectURL: (blob: Blob) => (created.push(blob), 'blob:x'),
        revokeObjectURL: (url: string) => void revoked.push(url),
      }),
    )
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderInProject(<ReaderGuidePage />, route)
    await screen.findByRole('article')

    await userEvent.click(screen.getByRole('button', { name: 'Print or save as PDF' }))
    expect(print).toHaveBeenCalled()
    const timers = vi.spyOn(window, 'setTimeout')
    await userEvent.click(screen.getByRole('button', { name: 'Download as Markdown' }))
    expect(click).toHaveBeenCalled()
    expect(await created[0].text()).toContain('version: 3')
    // The file's address is let go a while later, never while the download may be starting.
    expect(revoked).toEqual([])
    const later = timers.mock.calls.find(([, delay]) => (delay ?? 0) >= 10_000)
    expect(later).toBeDefined()
    ;(later?.[0] as () => void)()
    expect(revoked).toEqual(['blob:x'])
    timers.mockRestore()
    click.mockRestore()
  })

  it('says plainly when the guide cannot be made', async () => {
    mockApi({ ...base, 'GET /reader/guide': problem(404, 'not_found', 'That scene was not in the plan at version 2.') })
    renderInProject(<ReaderGuidePage />, { ...route, at: 'plan/pln_s/guide?version=2' })
    expect(await screen.findByRole('alert')).toHaveTextContent('That scene was not in the plan at version 2.')
    expect(screen.getByRole('button', { name: 'Print or save as PDF' })).toBeDisabled()
  })

  it('shows entries and lines that repeat without mixing them up', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockApi({
      ...base,
      'GET /reader/guide': guide({
        sections: [
          {
            heading: 'People',
            entries: [
              { name: 'Mara', lines: ['Mara is at the docks.', 'Mara is at the docks.'] },
              { name: 'Mara', lines: [] },
            ],
          },
        ],
      }),
    })
    renderInProject(<ReaderGuidePage />, route)
    const sheet = await screen.findByRole('article')
    expect(within(sheet).getAllByRole('heading', { name: 'Mara' })).toHaveLength(2)
    expect(within(sheet).getAllByText('Mara is at the docks.')).toHaveLength(2)
    expect(errors.mock.calls.some((call) => String(call[0]).includes('same key'))).toBe(false)
    errors.mockRestore()
  })

  it('says when only the latest versions are listed', async () => {
    const many = Array.from({ length: 100 }, (_, index) => revision(150 - index))
    mockApi({ ...base, 'GET /revisions': { items: many }, 'GET /reader/guide': guide() })
    renderInProject(<ReaderGuidePage />, { ...route, context: { headRevision: 150 } })
    expect(await screen.findByText(/Only the latest 100 versions are listed here/)).toBeInTheDocument()
  })

  it('shows an empty guide as such', async () => {
    mockApi({ ...base, 'GET /reader/guide': guide({ sections: [] }) })
    renderInProject(<ReaderGuidePage />, route)
    expect(await screen.findByText('Nobody has appeared yet')).toBeInTheDocument()
  })
})

describe('guideFileName', () => {
  it('keeps letters and plain punctuation', () => {
    expect(guideFileName('The story so far, after “The Docks” — version 3')).toBe(
      'The story so far, after The Docks version 3.md',
    )
    expect(guideFileName('“”')).toBe('reader guide.md')
  })
})
