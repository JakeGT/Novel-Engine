import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem, StudioJob } from '../api/client'
import { expectAccessible, mockApi, renderInProject, reply } from '../test/harness'
import { SuggestBridges, SuggestIntent } from './PlanHelp'

afterEach(() => vi.unstubAllGlobals())

const scene: PlanItem = {
  id: 'pln_room',
  kind: 'scene',
  title: 'The strongroom',
  summary: 'Mara goes after the ledger.',
  parent_id: null,
  position: 0,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 1,
}

function job(overrides: Partial<StudioJob>): StudioJob {
  return {
    id: 'job_1',
    kind: 'scene_intent',
    status: 'succeeded',
    stage: '',
    progress: 100,
    cancel_requested: false,
    prompt_tokens: 120,
    completion_tokens: 30,
    result: { mutation_id: 'mut_1', filled: ['goal'], dropped: 0 },
    ...overrides,
  } as StudioJob
}

describe('SuggestIntent', () => {
  it('shows the cost before anything is sent, then links to the suggestion in Review', async () => {
    const api = mockApi({
      'GET /plan/pln_room/intent/estimate': { total_tokens: 1800, reads_prose: true },
      'POST /plan/pln_room/intent/suggest': reply(202, job({ status: 'queued', progress: 0, result: {} })),
      'GET /jobs/job_1': job({}),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SuggestIntent scene={scene} />)

    // Nothing is estimated or sent until the author opens the offer.
    expect(api.calls).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Suggest what this scene is for' }))
    const offer = screen.getByRole('group', { name: 'Suggest what this scene is for' })
    expect(await within(offer).findByText(/About 1,800 tokens/)).toHaveTextContent(
      'reads this scene’s plan card and what you have written',
    )
    expect(api.find('POST', '/intent/suggest')).toHaveLength(0)
    await expectAccessible(container)

    await user.click(within(offer).getByRole('button', { name: 'Ask the assistant' }))
    const link = await screen.findByRole('link', { name: 'See the suggestion in Review' })
    expect(link).toHaveAttribute('href', '/projects/prj_1/review/mut_1')
    expect(api.find('POST', '/intent/suggest')).toHaveLength(1)
  })

  it('says plainly when nothing could be suggested', async () => {
    mockApi({
      'GET /plan/pln_room/intent/estimate': { total_tokens: 900, reads_prose: false },
      'POST /plan/pln_room/intent/suggest': reply(202, job({ status: 'queued', result: {} })),
      'GET /jobs/job_1': job({ result: { mutation_id: null, filled: [], dropped: 2 } }),
    })
    const user = userEvent.setup()
    renderInProject(<SuggestIntent scene={{ ...scene, intent: { goal: 'The ledger', obstacle: '' } }} />)

    await user.click(screen.getByRole('button', { name: 'Suggest the rest of what this scene is for' }))
    await user.click(await screen.findByRole('button', { name: 'Ask the assistant' }))
    expect(await screen.findByText(/Nothing to suggest: the assistant could not show/)).toBeInTheDocument()
  })

  it('is not offered once the intent is complete', () => {
    mockApi({})
    renderInProject(
      <SuggestIntent scene={{ ...scene, intent: { goal: 'a', obstacle: 'b', outcome: 'yes_but' } }} />,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a refusal next to the offer, in plain words', async () => {
    mockApi({
      'GET /plan/pln_room/intent/estimate': reply(400, {
        code: 'validation_error',
        detail: 'Say what happens in this scene, or write some of it, first: there is nothing to go on yet.',
      }),
    })
    const user = userEvent.setup()
    renderInProject(<SuggestIntent scene={{ ...scene, summary: '' }} />)
    await user.click(screen.getByRole('button', { name: 'Suggest what this scene is for' }))
    expect(await screen.findByText(/nothing to go on yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask the assistant' })).toBeDisabled()
  })
})

describe('SuggestBridges', () => {
  it('offers to bring about a preferred condition, saying it is not needed', async () => {
    mockApi({ 'GET /plan/pln_room/bridges/estimate': { total_tokens: 2400, reads_prose: false } })
    const user = userEvent.setup()
    renderInProject(<SuggestBridges scene={scene} index={0} preferred />)
    await user.click(screen.getByRole('button', { name: 'Suggest a scene to bring this about' }))
    expect(await screen.findByText(/About 2,400 tokens/)).toHaveTextContent(
      'The scene would rather have this, though it does not need it.',
    )
  })

  it('asks for bridge scenes for one condition, with the cost first', async () => {
    const api = mockApi({
      'GET /plan/pln_room/bridges/estimate': { total_tokens: 2400, reads_prose: false },
      'POST /plan/pln_room/bridges/suggest': reply(202, job({ kind: 'bridge_scenes', status: 'queued', result: {} })),
      'GET /jobs/job_1': job({ kind: 'bridge_scenes', result: { mutation_id: 'mut_2', bridges: 1, dropped: 0 } }),
    })
    const user = userEvent.setup()
    renderInProject(<SuggestBridges scene={scene} index={1} />)

    await user.click(screen.getByRole('button', { name: 'Suggest a scene to bridge this' }))
    expect(await screen.findByText(/About 2,400 tokens/)).toHaveTextContent('one or two scenes to come just before it')
    expect(api.find('GET', '/bridges/estimate')[0].search.get('condition')).toBe('1')
    await user.click(screen.getByRole('button', { name: 'Ask the assistant' }))
    await waitFor(() => expect(api.find('POST', '/bridges/suggest')).toHaveLength(1))
    expect(api.find('POST', '/bridges/suggest')[0].search.get('condition')).toBe('1')
    expect(await screen.findByRole('link', { name: 'See the suggestion in Review' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_2',
    )
  })
})
