import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem, StudioJob } from '../api/client'
import { expectAccessible, mockApi, renderInProject, reply } from '../test/harness'
import { SuggestGapScene, SuggestStages } from './PlanHelp'

afterEach(() => vi.unstubAllGlobals())

const arc: PlanItem = {
  id: 'pln_arc',
  kind: 'arc',
  title: 'Learning to trust',
  summary: 'Mara stops working alone.',
  parent_id: null,
  position: 0,
  refs: [{ entity_id: 'ent_mara', role: 'about' }],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  path: [],
  created_revision: 1,
}
const thread: PlanItem = { ...arc, id: 'pln_thread', kind: 'thread', title: 'Who took the ledger?', refs: [] }

function job(overrides: Partial<StudioJob>): StudioJob {
  return {
    id: 'job_1',
    kind: 'arc_stages',
    status: 'succeeded',
    stage: '',
    progress: 100,
    cancel_requested: false,
    prompt_tokens: 120,
    completion_tokens: 30,
    result: { mutation_id: 'mut_1', stages: 3, dropped: 0, cleared: 0 },
    ...overrides,
  } as StudioJob
}

describe('SuggestStages', () => {
  it('shows what it reads and its cost first, then links to the suggestion in Review', async () => {
    const api = mockApi({
      'GET /plan/pln_arc/stages/estimate': { total_tokens: 2100, reads_prose: false },
      'POST /plan/pln_arc/stages/suggest': reply(202, job({ status: 'queued', result: {} })),
      'GET /jobs/job_1': job({}),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SuggestStages arc={arc} who="Mara" />)
    expect(api.calls).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Suggest the stages' }))
    const offer = screen.getByRole('group', { name: 'Suggest the stages' })
    expect(await within(offer).findByText(/About 2,100 tokens/)).toHaveTextContent(
      'what your story bible records about Mara',
    )
    await expectAccessible(container)
    await user.click(within(offer).getByRole('button', { name: 'Ask the assistant' }))
    expect(await screen.findByRole('link', { name: 'See the suggestion in Review' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_1',
    )
  })

  it('is not offered for an arc with stages or with nobody it is about', () => {
    mockApi({})
    const { unmount } = renderInProject(<SuggestStages arc={{ ...arc, path: [{ name: 'Alone' }] }} who="Mara" />)
    expect(screen.queryByRole('button', { name: 'Suggest the stages' })).not.toBeInTheDocument()
    unmount()
    renderInProject(<SuggestStages arc={{ ...arc, refs: [] }} who="Mara" />)
    expect(screen.queryByRole('button', { name: 'Suggest the stages' })).not.toBeInTheDocument()
  })
})

describe('SuggestGapScene', () => {
  it('asks for a scene that moves a quiet thread along', async () => {
    const api = mockApi({
      'GET /plan/pln_thread/gap/estimate': { total_tokens: 1400, reads_prose: false },
      'POST /plan/pln_thread/gap/suggest': reply(202, job({ status: 'queued', result: {} })),
      'GET /jobs/job_1': job({ kind: 'gap_bridge', result: { mutation_id: null, bridges: 0, dropped: 1 } }),
    })
    const user = userEvent.setup()
    renderInProject(<SuggestGapScene item={thread} />)
    await user.click(screen.getByRole('button', { name: 'Suggest a scene' }))
    expect(await screen.findByText(/moves the thread along, just before the scene that resolves it/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Ask the assistant' }))
    expect(await screen.findByText(/Nothing to suggest: the scene the assistant offered/)).toBeInTheDocument()
    expect(api.find('POST', '/gap/suggest')).toHaveLength(1)
  })

  it('names the stage of an arc in the request', async () => {
    const api = mockApi({
      'GET /plan/pln_arc/gap/estimate': { total_tokens: 1400, reads_prose: false },
    })
    const user = userEvent.setup()
    renderInProject(<SuggestGapScene item={arc} stage={2} />)
    await user.click(screen.getByRole('button', { name: 'Suggest a scene' }))
    expect(await screen.findByText(/one scene where the stage is reached/)).toBeInTheDocument()
    expect(api.find('GET', '/gap/estimate')[0].search.get('stage')).toBe('2')
  })
})
