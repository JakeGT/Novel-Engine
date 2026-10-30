import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { partsAbove } from '../lib/plan'
import { SceneContextDialog } from './SceneContext'

afterEach(() => vi.unstubAllGlobals())

const plan = (id: string, kind: string, title: string, parent: string | null) => ({
  id,
  kind,
  title,
  summary: '',
  parent_id: parent,
  position: 0,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 2,
})

const arc = plan('plan_arc', 'arc', 'The homecoming', null)
const beat = plan('plan_beat', 'beat', 'The fog', 'plan_arc')
const scene = plan('plan_1', 'scene', 'The Docks', 'plan_beat')

const item = (key: string, section: string, title: string, text: string, reason: string) => ({
  key,
  section,
  section_title: title,
  text,
  reason,
  authority: 'writing',
  record_type: 'source_version',
  record_id: key,
  location: null,
  words: text.split(' ').length,
  score: 2,
  hard: false,
  pinned: false,
})

const later = {
  ...item(
    'nearby:plan_9',
    'nearby',
    'How other recent scenes end',
    'From the end of “Years later”: Mara was old now.',
    'It is how “Years later” ends, as you wrote it. It happens later in the story’s time than this scene.',
  ),
}

const back = {
  ...item(
    'summary:plan_0',
    'earlier',
    'What happened in earlier scenes',
    '“The arrival”, as planned: Mara comes home.',
    'It is what the plan card says. In the story’s time it happens 4 years before this scene.',
  ),
  signals: [{ name: 'story time', effect: -1.2, note: 'In the story’s time it happens 4 years before this scene.' }],
}

const context = {
  scene_id: 'plan_1',
  point_of_view: null,
  settings: { pins: [], excludes: [], budget: null, include_secrets: false },
  policy_version: 'scene-draft/3',
  budget: 2500,
  words: 8,
  checksum: 'abcdef0123456789',
  items: [back],
  left_out: [{ reason: 'later', item: later }],
  index: { texts: 0, indexed: 0, behind: [], names_as_of: null, semantic: false },
  rendered: '',
}

const sceneSummary = {
  plan_id: 'plan_1',
  has_prose: false,
  version_id: null,
  version_number: null,
  plan_card: null,
  kept: null,
  kept_out_of_date: false,
  suggested: [],
  max_words: 120,
}

const part = (id: string, kind: string, title: string, extra: object = {}) => ({
  plan_id: id,
  kind,
  title,
  scenes: 3,
  unsummarised: ['The Docks'],
  derived: null,
  kept: null,
  kept_out_of_date: false,
  suggested: [],
  max_words: 200,
  ...extra,
})

const suggestion = {
  id: 'psum_1',
  body: 'Mara comes home through the fog.',
  state: 'proposed',
  origin: 'model',
  scenes: 2,
  created_at: '2026-10-21T09:00:00Z',
  model: 'stand-in',
  prompt_version: 'part-summary/1',
  job_id: 'job_1',
  decided_at: null,
}

const routes = (extra: Record<string, unknown> = {}) => ({
  'GET /entities': { items: [entity()], limit: 200, offset: 0 },
  'GET /plan': { items: [arc, beat, scene], open: [], promises: [] },
  'GET /plan/plan_1/context': context,
  'GET /plan/plan_1/context/kept': [],
  'GET /plan/plan_1/summary': sceneSummary,
  'GET /plan/plan_arc/part-summary': part('plan_arc', 'arc', 'The homecoming'),
  ...extra,
})

describe('summaries of whole beats and arcs, and story time in the context', () => {
  it('finds the parts a scene sits in, nearest first', () => {
    expect(partsAbove(scene as never, [arc, beat, scene] as never).map((p) => p.id)).toEqual([
      'plan_beat',
      'plan_arc',
    ])
    expect(partsAbove(arc as never, [arc, beat, scene] as never)).toEqual([])
  })

  it('says what is left out for coming later in the story’s time, and what story time lowered', async () => {
    mockApi(
      routes({
        'GET /plan/plan_beat/part-summary': part('plan_beat', 'beat', 'The fog', {
          derived: 'Tobin waits in the fog.',
        }),
      }),
    )
    const { container } = renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const dialog = await screen.findByRole('dialog', { name: 'What the assistant would use for “The Docks”' })
    const left = await within(dialog).findByRole('region', { name: 'Left out' })
    expect(left).toHaveTextContent('From later in the story’s time than this scene')
    expect(left).toHaveTextContent('It happens later in the story’s time than this scene.')
    expect(within(left).getByRole('button', { name: 'Use it' })).toBeInTheDocument()
    const earlier = within(dialog).getByRole('region', { name: 'What happened in earlier scenes' })
    expect(earlier).toHaveTextContent('Lowered: Further back in the story’s time')

    const beatPanel = await within(dialog).findByRole('region', { name: 'Summary of the beat “The fog”' })
    expect(await within(beatPanel).findByText('Tobin waits in the fog.')).toBeInTheDocument()
    expect(beatPanel).toHaveTextContent('Worked out from the summaries you kept of its scenes.')
    expect(beatPanel).toHaveTextContent('Not covered, because they have no kept summary: The Docks.')
    const arcPanel = within(dialog).getByRole('region', { name: 'Summary of the arc “The homecoming”' })
    expect(await within(arcPanel).findByText(/none of its 3 scenes has a kept summary/)).toBeInTheDocument()
    // Nothing to suggest from: no scene summaries in the arc.
    expect(within(arcPanel).queryByRole('button', { name: 'Ask the assistant to suggest one' })).toBeNull()
    await expectAccessible(container)
  })

  it('keeps a correction of a suggestion and writes the author’s own', async () => {
    const withSuggestion = part('plan_beat', 'beat', 'The fog', {
      derived: 'Tobin waits in the fog.',
      suggested: [suggestion],
    })
    const kept = part('plan_beat', 'beat', 'The fog', {
      derived: 'Tobin waits in the fog.',
      kept: { ...suggestion, state: 'accepted', origin: 'model_corrected', body: 'Mara comes home.' },
    })
    const api = mockApi(
      routes({
        'GET /plan/plan_beat/part-summary': withSuggestion,
        'POST /plan/plan_beat/part-summary/psum_1': kept,
        'PUT /plan/plan_beat/part-summary': {
          ...kept,
          kept: { ...suggestion, state: 'accepted', origin: 'author', body: 'Tobin gives up.' },
        },
        'POST /plan/plan_beat/part-summary/suggest': { id: 'job_9', kind: 'part_summary', status: 'queued' },
        'GET /jobs/job_9': { id: 'job_9', kind: 'part_summary', status: 'succeeded' },
      }),
    )
    const user = userEvent.setup()
    renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const panel = await screen.findByRole('region', { name: 'Summary of the beat “The fog”' })
    expect(await within(panel).findByText('The assistant suggests')).toBeInTheDocument()
    expect(panel).toHaveTextContent('From 2 scene summaries. Not used until you keep it.')

    await user.click(within(panel).getByRole('button', { name: 'Correct it' }))
    const box = within(panel).getByLabelText('Correct the suggestion, then keep it')
    await user.clear(box)
    await user.type(box, 'Mara comes home.')
    await user.click(within(panel).getByRole('button', { name: 'Keep my correction' }))
    await waitFor(() => expect(api.find('POST', '/plan/plan_beat/part-summary/psum_1')).toHaveLength(1))
    expect(api.find('POST', '/plan/plan_beat/part-summary/psum_1')[0].body).toEqual({
      accept: true,
      body: 'Mara comes home.',
    })
    expect(await within(panel).findByText('Suggested by the assistant and corrected by you')).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: 'Rewrite the summary' }))
    const mine = within(panel).getByLabelText('Your summary of the whole beat')
    await user.clear(mine)
    await user.type(mine, 'Tobin gives up.')
    expect(panel).toHaveTextContent('3 words of 200 at most')
    await user.click(within(panel).getByRole('button', { name: 'Keep my summary' }))
    await waitFor(() => expect(api.find('PUT', '/plan/plan_beat/part-summary')).toHaveLength(1))
    expect(api.find('PUT', '/plan/plan_beat/part-summary')[0].body).toEqual({ body: 'Tobin gives up.' })
    expect(await within(panel).findByText('Written by you')).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: 'Ask the assistant to suggest one' }))
    await waitFor(() => expect(api.find('POST', '/plan/plan_beat/part-summary/suggest')).toHaveLength(1))
  })

  it('says when a kept summary is out of date', async () => {
    mockApi(
      routes({
        'GET /plan/plan_beat/part-summary': part('plan_beat', 'beat', 'The fog', {
          derived: 'Tobin waits.',
          kept: { ...suggestion, state: 'accepted', origin: 'author' },
          kept_out_of_date: true,
        }),
      }),
    )
    renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const panel = await screen.findByRole('region', { name: 'Summary of the beat “The fog”' })
    expect(await within(panel).findByText(/Out of date: a scene summary in it has changed since/)).toBeInTheDocument()
  })
})
