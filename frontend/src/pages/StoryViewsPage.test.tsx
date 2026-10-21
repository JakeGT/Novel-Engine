import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { StoryViewsPage } from './StoryViewsPage'

afterEach(() => vi.restoreAllMocks())

const view = (overrides: Record<string, unknown> = {}) => ({
  view: 'location',
  version: 12,
  latest_version: 12,
  behind: 0,
  rows: 40,
  answers_reads: true,
  trusted: true,
  verified_at: '2026-10-21T09:00:00Z',
  verified_version: 12,
  outcome: 'matched',
  differences: null,
  built_at: '2026-10-21T09:00:00Z',
  lag_seconds: null,
  state: 'Ready',
  ...overrides,
})

const open = () => renderInProject(<StoryViewsPage />, { path: 'story-views', at: 'story-views' })

describe('StoryViewsPage', () => {
  it('says for each view whether reads use it, how far it is, and how it was checked', async () => {
    mockApi({
      'GET /story-views': {
        line: 'Main story',
        main_story: true,
        how_built: 'Each view is built again in full from the story’s records after a change.',
        views: [
          view(),
          view({
            view: 'timeline',
            version: 10,
            behind: 2,
            answers_reads: false,
            lag_seconds: 90,
            state: 'Catching up',
          }),
          view({
            view: 'dependency',
            answers_reads: false,
            trusted: false,
            outcome: 'differed',
            differences: 3,
            state: 'Differed when checked',
          }),
          view({
            view: 'disclosure',
            version: null,
            rows: 0,
            answers_reads: false,
            trusted: false,
            verified_version: null,
            verified_at: null,
            outcome: null,
            built_at: null,
            state: 'Not built yet',
          }),
        ],
      },
    })
    const { container } = open()
    expect(await screen.findByRole('heading', { name: 'Ready views of the story' })).toBeInTheDocument()
    expect(await screen.findByText(/For the main story\. 1 of 4 is ready\./)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Where everyone is' })).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Catching up')).toBeInTheDocument()
    expect(
      screen.getByText(/Built at version 10; the story is at version 12, 2 changes behind · waiting 2 minutes to catch up/),
    ).toBeInTheDocument()
    expect(screen.getByText(/3 items differed, so it is not used/)).toBeInTheDocument()
    expect(screen.getAllByText(/Not yet checked against the story’s history/)).toHaveLength(1)
    expect(screen.getByText(/Until it catches up, reads work the answer out from the records\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Timeline' })).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('lets an owner check the views against the history now', async () => {
    const api = mockApi({
      'GET /story-views': { line: 'Main story', main_story: true, how_built: '', views: [view({ answers_reads: false, state: 'Not checked yet', verified_version: null, outcome: null })] },
      'POST /story-views/check': { checked: 6, matched: 5, differed: 1, skipped: 0 },
    })
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Check them against the history now' }))
    expect(await screen.findByText(/Checked 6 views: 5 matched, 1 differed\./)).toBeInTheDocument()
    expect(api.find('POST', '/story-views/check')).toHaveLength(1)
  })

  it('names the what-if the views are for', async () => {
    mockApi({ 'GET /story-views': { line: 'If Mara stayed', main_story: false, how_built: '', views: [view()] } })
    open()
    expect(await screen.findByText(/For the what-if “If Mara stayed”\. All of them are ready\./)).toBeInTheDocument()
  })

  it('says plainly when the views cannot be loaded', async () => {
    mockApi({ 'GET /story-views': problem(503, 'unavailable', 'The story is not available right now.') })
    open()
    expect(await screen.findByText(/The story is not available right now/)).toBeInTheDocument()
  })
})
