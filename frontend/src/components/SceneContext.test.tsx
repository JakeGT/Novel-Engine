import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { SceneContextDialog } from './SceneContext'

afterEach(() => vi.unstubAllGlobals())

const scene = {
  id: 'plan_1',
  kind: 'scene',
  title: 'The Docks',
  summary: '',
  parent_id: null,
  position: 0,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 2,
}

const item = (key: string, section: string, title: string, text: string, reason: string, hard = false) => ({
  key,
  section,
  section_title: title,
  text,
  reason,
  authority: 'fact',
  record_type: 'fact',
  record_id: key,
  location: null,
  words: text.split(' ').length,
  score: 1,
  hard,
  pinned: false,
})

const context = {
  scene_id: 'plan_1',
  point_of_view: 'ent_mara',
  settings: { pins: [], excludes: [], budget: null, include_secrets: false },
  policy_version: 'scene-draft/3',
  budget: 2500,
  words: 12,
  checksum: 'abcdef0123456789',
  items: [
    item('fact:1', 'true', 'What is true in the story', 'Mara is located at Lantern Harbor.', 'It is about Mara, who is in the scene.'),
    item('scene:plan_1', 'scene', 'The scene', 'The Docks.', 'This is the scene to write.', true),
  ],
  left_out: [
    {
      reason: 'secret',
      item: item('fact:2', 'secrets', 'Only for you', 'Mara is a member of the guild.', 'It is a secret.'),
    },
  ],
  index: { texts: 0, indexed: 0, behind: [], names_as_of: null, semantic: false },
  rendered: '## The scene\n- The Docks.\n',
}

const summaries = {
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

const suggestion = {
  id: 'sum_1',
  body: 'Mara comes home and Tobin does not greet her.',
  state: 'proposed',
  origin: 'model',
  version_id: 'ver_1',
  created_at: '2026-10-20T09:00:00Z',
  model: 'stand-in',
  prompt_version: 'scene-summary/1',
  job_id: 'job_1',
  decided_at: null,
}

describe('SceneContextDialog', () => {
  it('lists what would be given with why, and leaves out on request', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': context,
      'GET /plan/plan_1/context/kept': [],
      'GET /plan/plan_1/summary': summaries,
      'PUT /plan/plan_1/context': context,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const dialog = await screen.findByRole('dialog', { name: 'What the assistant would use for “The Docks”' })
    expect(await within(dialog).findByText(/About 12 words/)).toBeInTheDocument()
    expect(within(dialog).getByText(/It is about Mara, who is in the scene/)).toBeInTheDocument()
    expect(within(dialog).getByText('Always given')).toBeInTheDocument()
    expect(within(dialog).getByText('Secrets the viewpoint character does not know')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(dialog).getByRole('button', { name: 'Don’t use' }))
    await waitFor(() => expect(api.find('PUT', '/plan/plan_1/context')).toHaveLength(1))
    expect(api.find('PUT', '/plan/plan_1/context')[0].body).toEqual({
      pins: [],
      excludes: ['fact:1'],
      budget: null,
      include_secrets: false,
      expected_revision: 3,
    })
  })

  it('compares the context now with the one a kept draft was written from', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': context,
      'GET /plan/plan_1/summary': summaries,
      'GET /plan/plan_1/context/kept': [
        { manifest_id: 'ctx_1', version_id: 'ver_2', version_number: 2, kept_at: '2026-10-12T09:30:00Z', words: 10 },
      ],
      'GET /plan/plan_1/context/kept/ctx_1': {
        manifest_id: 'ctx_1',
        kept_at: '2026-10-12T09:30:00Z',
        words_then: 10,
        words_now: 12,
        budget_then: 2500,
        budget_now: 2500,
        rules_changed: false,
        same: false,
        added: [item('intent:plan_1', 'scene', 'The scene', 'Mara wants: find Tobin.', 'You said what the viewpoint character wants here and how it ends.', true)],
        removed: [
          {
            key: 'fact:9',
            section_title: 'What is true in the story',
            text: 'Tobin is a pilot.',
            reason: 'It is about Tobin, who is in the scene.',
            words: 4,
            left_out: 'excluded',
            now: null,
          },
          {
            key: 'passage:src_1:0',
            section_title: 'From the author’s own writing',
            text: null,
            reason: 'It mentions Tobin.',
            words: 40,
            left_out: null,
            now: null,
          },
        ],
        changed: [
          {
            item: item('scene:plan_1', 'scene', 'The scene', 'The Docks. Tobin gives up.', 'This is the scene to write.', true),
            text_then: 'The Docks. Tobin watches the water.',
            reason_then: 'This is the scene to write.',
            section_title_then: 'The scene',
            what: ['words'],
          },
        ],
        unchanged: 5,
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const dialog = await screen.findByRole('dialog', { name: 'What the assistant would use for “The Docks”' })
    const choose = await within(dialog).findByLabelText('Compare with what a kept draft was written from')
    expect(choose).toHaveValue('')
    expect(api.find('GET', '/context/kept/ctx_1')).toHaveLength(0)
    await user.selectOptions(choose, 'ctx_1')

    const compare = within(dialog).getByRole('region', { name: 'Compare with a kept draft' })
    expect(await within(compare).findByText(/Since version 2 was drafted: 1 added, 2 no longer given, 1 changed/)).toBeInTheDocument()
    expect(compare).toHaveTextContent('5 things the same')
    expect(compare).toHaveTextContent('Mara wants: find Tobin.')
    expect(compare).toHaveTextContent('Tobin is a pilot.')
    expect(compare).toHaveTextContent('You said not to use it')
    expect(compare).toHaveTextContent('A passage of your writing. It mentions Tobin.')
    expect(compare).toHaveTextContent('Before: The Docks. Tobin watches the water.')
    expect(compare).toHaveTextContent('What is different: its words')
    await expectAccessible(container)
  })

  it('says how to get something to compare when no draft has been kept', async () => {
    mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': context,
      'GET /plan/plan_1/context/kept': [],
      'GET /plan/plan_1/summary': summaries,
    })
    renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    expect(await screen.findByText(/When you keep a draft of this scene, you can compare/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Compare with what a kept draft was written from')).not.toBeInTheDocument()
  })

  it('says what moved each item, how far behind the index is, and keeps a summary only when asked', async () => {
    const ranked = {
      ...item(
        'summary:plan_0',
        'earlier',
        'What happened in earlier scenes',
        '“The arrival”, as planned: Mara comes home.',
        'It is what the plan card of “The arrival” says happens. It is from “The arrival”, two scenes back.',
      ),
      signals: [
        { name: 'distance', effect: -0.6, note: 'It is from “The arrival”, two scenes back.' },
        { name: 'importance', effect: 1.2, note: 'You marked Mara as one of your main entries.' },
      ],
    }
    const withLayers = {
      ...context,
      items: [ranked, ...context.items],
      index: { ...context.index, texts: 2, indexed: 1, waiting: 1, waiting_seconds: 300, failing: 1 },
    }
    const written = { ...summaries, has_prose: true, version_id: 'ver_1', version_number: 1, plan_card: 'Mara comes home.' }
    const kept = {
      ...written,
      kept: { ...suggestion, state: 'accepted', origin: 'model_corrected', body: 'Mara comes home.' },
    }
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': withLayers,
      'GET /plan/plan_1/context/kept': [],
      'GET /plan/plan_1/summary': { ...written, suggested: [suggestion] },
      'POST /plan/plan_1/summary/sum_1': kept,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const dialog = await screen.findByRole('dialog', { name: 'What the assistant would use for “The Docks”' })
    const earlier = await within(dialog).findByRole('region', { name: 'What happened in earlier scenes' })
    expect(earlier).toHaveTextContent('Lowered: Further from this scene')
    expect(earlier).toHaveTextContent('Raised: Important to you')
    expect(earlier).toHaveTextContent('two scenes back')
    expect(dialog).toHaveTextContent(
      '1 text you changed is still being read in for about 5 minutes; what it says may be missing here for now. 1 text could not be read and will be tried again.',
    )

    const panel = within(dialog).getByRole('region', { name: 'Summary for later scenes' })
    expect(await within(panel).findByText('The assistant suggests')).toBeInTheDocument()
    expect(panel).toHaveTextContent('No summary kept yet. Later scenes are given what the plan card says: “Mara comes home”.')
    expect(panel).toHaveTextContent('The assistant suggests')
    expect(panel).toHaveTextContent('Not used until you keep it.')
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: 'Correct it' }))
    const box = within(panel).getByLabelText('Correct the suggestion, then keep it')
    await user.clear(box)
    await user.type(box, 'Mara comes home.')
    await user.click(within(panel).getByRole('button', { name: 'Keep my correction' }))
    await waitFor(() => expect(api.find('POST', '/plan/plan_1/summary/sum_1')).toHaveLength(1))
    expect(api.find('POST', '/plan/plan_1/summary/sum_1')[0].body).toEqual({ accept: true, body: 'Mara comes home.' })
    expect(await within(panel).findByText('Suggested by the assistant and corrected by you')).toBeInTheDocument()
  })

  it('asks the assistant for a summary and writes the author’s own', async () => {
    const written = { ...summaries, has_prose: true, version_id: 'ver_1', version_number: 1 }
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': context,
      'GET /plan/plan_1/context/kept': [],
      'GET /plan/plan_1/summary': written,
      'PUT /plan/plan_1/summary': {
        ...written,
        kept: { ...suggestion, state: 'accepted', origin: 'author', body: 'Tobin waits.' },
      },
      'POST /plan/plan_1/summary/suggest': { id: 'job_9', kind: 'scene_summary', status: 'queued' },
      'GET /jobs/job_9': { id: 'job_9', kind: 'scene_summary', status: 'succeeded' },
    })
    const user = userEvent.setup()
    renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const panel = await screen.findByRole('region', { name: 'Summary for later scenes' })
    await user.click(await within(panel).findByRole('button', { name: 'Ask the assistant to suggest one' }))
    await waitFor(() => expect(api.find('POST', '/plan/plan_1/summary/suggest')).toHaveLength(1))

    await user.click(within(panel).getByRole('button', { name: 'Write a summary' }))
    await user.type(within(panel).getByLabelText('Your summary of the scene as it is written now'), 'Tobin waits.')
    expect(panel).toHaveTextContent('2 words of 120 at most')
    await user.click(within(panel).getByRole('button', { name: 'Keep my summary' }))
    await waitFor(() => expect(api.find('PUT', '/plan/plan_1/summary')).toHaveLength(1))
    expect(api.find('PUT', '/plan/plan_1/summary')[0].body).toEqual({ body: 'Tobin waits.' })
    expect(await within(panel).findByText('Written by you')).toBeInTheDocument()
  })
})
