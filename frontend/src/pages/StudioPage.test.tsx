import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem, SceneDraft, Studio, StudioJob } from '../api/client'
import { entity, mutation, operation } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply, VIEWER, type RecordedCall } from '../test/harness'
import { StudioPage } from './StudioPage'

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const TEXT = 'Ines climbed to the lamp room. The weight of the night pressed on her. Tobin Ashe waited by the glass.'
const at = (phrase: string, text = TEXT) => ({ start: text.indexOf(phrase), end: text.indexOf(phrase) + phrase.length })
const DRAFT_ONE = 'The stairs wound up and up. Ines counted them.'
const DRAFT_TWO = 'She did not look at the sea.'

const scene: PlanItem = {
  id: 'pln_s',
  kind: 'scene',
  title: 'The lamp room at night',
  summary: 'Ines compares the two charts.',
  parent_id: null,
  position: 0,
  refs: [
    { entity_id: 'ent_ines', role: 'point_of_view' },
    { entity_id: 'ent_point', role: 'place' },
  ],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 1,
}

function job(overrides: Partial<StudioJob> = {}): StudioJob {
  return {
    id: 'job_1',
    kind: 'scene_draft',
    status: 'running',
    stage: 'Writing draft 1 of 2',
    progress: 40,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_prompt_tokens: 0,
    automatic: false,
    result: {},
    created_at: '2026-10-17T10:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

function draft(overrides: Partial<SceneDraft> = {}): SceneDraft {
  return {
    id: 'drf_1',
    number: 1,
    job_id: 'job_1',
    manifest_id: 'man_1',
    model: 'a-model',
    content: DRAFT_ONE,
    words: 9,
    created_at: '2026-10-17T10:01:00Z',
    checks: [],
    assistant: [],
    ...overrides,
  }
}

function studio(overrides: Partial<Studio> = {}): Studio {
  return {
    scene_id: 'pln_s',
    title: 'The lamp room at night',
    text_id: 'src_1',
    version_id: 'ver_2',
    content: TEXT,
    words: 20,
    versions: [
      { id: 'ver_2', number: 2, words: 20, origin: 'assistant', draft_id: 'drf_0', created_at: '2026-10-17T09:00:00Z' },
      { id: 'ver_1', number: 1, words: 12, origin: 'author', created_at: '2026-10-16T09:00:00Z' },
    ],
    checks: [
      { code: 'stock_phrase', key: 'k_stock', severity: 'note', message: 'A phrase readers often meet: “the weight of”.', ...at('The weight of'), came_back: false },
      { code: 'new_name', key: 'k_name', severity: 'warning', message: 'Tobin Ashe is not in your story bible yet.', ...at('Tobin Ashe'), came_back: false },
    ],
    assistant: [
      {
        id: 'fnd_1',
        key: 'k_note',
        category: 'viewpoint',
        message: 'Ines could not see who waited from the stairs.',
        suggestion: 'Let her hear him first.',
        ...at('waited by the glass'),
      },
    ],
    drafts: [],
    jobs: [],
    reading: { changed_since: true, discarded: 0, passages: 0, proposals: [] },
    ...overrides,
  }
}

const readAt = new Date(Date.now() - 2 * 3600 * 1000).toISOString()
const withOps = (id: string, count: number) =>
  mutation({ id, operations: Array.from({ length: count }, (_, index) => operation({ ordinal: index })) })
const storyBible = () => screen.getByRole('heading', { name: 'Your story bible' }).closest('section') as HTMLElement

const PASSES = [
  ['viewpoint', 'Viewpoint and secrets', 1400],
  ['continuity', 'Continuity with the story bible', 2100],
  ['plan', 'What the plan asks for', 1100],
  ['voice', 'Your voice', 1300],
  ['clarity', 'Clarity', 900],
] as const

const checkEstimate = {
  prompt_tokens: 3800,
  completion_tokens: 3000,
  total_tokens: 6800,
  context_words: 400,
  passes: PASSES.map(([check_pass, label, total]) => ({
    check_pass,
    label,
    description: `What ${label.toLowerCase()} looks at.`,
    prompt_tokens: total - 600,
    completion_tokens: 600,
    total_tokens: total,
  })),
}

const base = {
  'GET /studio/check-estimate': checkEstimate,
  'GET /entities': {
    items: [
      entity({ id: 'ent_ines', name: 'Ines Varga', aliases: [] }),
      entity({ id: 'ent_point', type: 'Location', name: 'Kestrel Point', aliases: [] }),
    ],
    limit: 200,
    offset: 0,
  },
  'GET /plan': { items: [scene], open: [], conditions: [] },
  'GET /ai/usage': { used_tokens: 0, limit_tokens: null, period_hours: 24 },
  'GET /links': { items: [] },
  'GET /pictures': { items: [] },
  'GET /checks/runs': { items: [] },
}
const route = { path: 'plan/:planId/write', at: 'plan/pln_s/write' }

describe('StudioPage', () => {
  it('shows the scene to write, with checks and notes beside it, each pointing at its passage', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)

    expect(await screen.findByRole('heading', { level: 1, name: 'The lamp room at night' })).toBeInTheDocument()
    expect(screen.getByText('Ines compares the two charts.')).toBeInTheDocument()
    expect(await screen.findByText(/Seen by/)).toHaveTextContent('Seen by Ines Varga')
    const box = screen.getByRole('textbox', { name: 'The text of “The lamp room at night”' }) as HTMLTextAreaElement
    expect(box).toHaveValue(TEXT)
    expect(screen.getByText(/20 words · Saved as version 2/)).toBeInTheDocument()

    // Warnings come before notes, and say so in words, not only colour.
    const checks = screen.getByRole('heading', { name: 'Checks' }).closest('section') as HTMLElement
    const items = within(checks).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Worth checking')
    expect(items[0]).toHaveTextContent('Tobin Ashe is not in your story bible yet.')
    expect(items[1]).toHaveTextContent('Note')

    // Going to a passage selects it in the text.
    await user.click(within(items[0]).getByRole('button', { name: 'Show in the text' }))
    expect(box.selectionStart).toBe(at('Tobin Ashe').start)
    expect(box.selectionEnd).toBe(at('Tobin Ashe').end)

    const notes = screen.getByRole('heading', { name: 'The assistant’s notes' }).closest('section') as HTMLElement
    expect(notes).toHaveTextContent('Viewpoint')
    expect(notes).toHaveTextContent('Suggestion: Let her hear him first.')
    expect(within(notes).getByText('waited by the glass').tagName).toBe('Q')

    const versions = screen.getByRole('table')
    expect(within(versions).getAllByRole('row')[1]).toHaveTextContent('2 · current20The assistant')
    expect(within(versions).getAllByRole('row')[2]).toHaveTextContent('You')
    await expectAccessible(container)
  })

  it('saves on top of the version it was written on, and keeps the words through a clash', async () => {
    let saves = 0
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': () => studio(saves > 0 ? { version_id: 'ver_3' } : {}),
      'PUT /plan/pln_s/studio/text': (call: RecordedCall) => {
        saves += 1
        if (saves === 1) return problem(409, 'conflict', 'This scene changed while you were writing. Your words are still here.')
        return studio({ version_id: 'ver_4', content: (call.body as { content: string }).content })
      },
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const box = await screen.findByRole('textbox', { name: /The text of/ })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.type(box, ' She turned.')
    expect(screen.getByText(/22 words · Not saved yet/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))

    const clash = await screen.findByRole('alert')
    expect(clash).toHaveTextContent('Someone else saved this scene while you were writing. Nothing was saved, and your words are still here.')
    expect(box).toHaveValue(`${TEXT} She turned.`)
    expect(api.find('PUT', '/studio/text')[0].body).toEqual({ content: `${TEXT} She turned.`, base_version_id: 'ver_2' })

    await waitFor(() => expect(within(clash).getByRole('button', { name: 'Save mine as the newest version' })).toBeEnabled())
    await user.click(within(clash).getByRole('button', { name: 'Save mine as the newest version' }))
    await waitFor(() => expect(api.find('PUT', '/studio/text')).toHaveLength(2))
    expect(api.find('PUT', '/studio/text')[1].body).toEqual({ content: `${TEXT} She turned.`, base_version_id: 'ver_3' })
    expect(await screen.findByText(/Saved as version 2/)).toBeInTheDocument()
  })

  it('brings back unsaved words left from last time', async () => {
    window.localStorage.setItem(
      'novel-engine.studio.prj_1.main.pln_s',
      JSON.stringify({ text: 'My unsaved start.', base: 'ver_2' }),
    )
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const box = await screen.findByRole('textbox', { name: /The text of/ })
    expect(box).toHaveValue('My unsaved start.')
    expect(screen.getByText('Your unsaved words from last time are back.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Put back the saved text' }))
    expect(box).toHaveValue(TEXT)
    expect(window.localStorage.getItem('novel-engine.studio.prj_1.main.pln_s')).toBeNull()
  })

  it('shows what a draft will read and cost before sending, then its progress, with a way to stop', async () => {
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio(),
      'POST /plan/pln_s/studio/estimate': (call: RecordedCall) => ({
        prompt_tokens: 3000,
        completion_tokens: 2000 * (call.body as { variants: number }).variants,
        total_tokens: 3000 + 2000 * (call.body as { variants: number }).variants,
        context_words: 1800,
      }),
      'POST /plan/pln_s/studio/drafts': reply(202, job({ status: 'queued', stage: '', progress: 0 })),
      'GET /jobs/job_1': job(),
      'POST /jobs/job_1/cancel': job({ cancel_requested: true }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)

    await user.click(await screen.findByRole('button', { name: 'Draft with the assistant' }))
    const form = screen.getByRole('region', { name: 'Draft with the assistant' })
    expect(await within(form).findByText(/It will read about/)).toHaveTextContent(
      'It will read about 1,800 words from your story bible, plan and writing, and write about 800 words. About 5,000 tokens in all (the measure AI providers charge by).',
    )
    await user.click(within(form).getByRole('button', { name: '2' }))
    expect(await within(form).findByText(/across 2 drafts/)).toHaveTextContent('write about 1,600 words across 2 drafts')
    await user.type(within(form).getByLabelText('Anything it should do (optional)'), 'End before he answers.')
    await expectAccessible(container)
    await user.click(within(form).getByRole('button', { name: 'Write 2 drafts' }))

    await waitFor(() => expect(api.find('POST', '/studio/drafts')).toHaveLength(1))
    expect(api.find('POST', '/studio/drafts')[0].body).toEqual({ variants: 2, words: 800, direction: 'End before he answers.' })
    const sent = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/studio/drafts'))
    expect(new Headers(sent?.[1]?.headers).get('Idempotency-Key')).toBeTruthy()

    const progress = await screen.findByRole('region', { name: 'Work in progress' })
    expect(await within(progress).findByText(/Writing draft 1 of 2/)).toBeInTheDocument()
    expect(within(progress).getByRole('progressbar', { name: 'How far along' })).toHaveAttribute('value', '40')
    await user.click(within(progress).getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(api.find('POST', '/jobs/job_1/cancel')).toHaveLength(1))
    expect(await within(progress).findByRole('button', { name: 'Stopping…' })).toBeDisabled()
  })

  it('says plainly why a draft failed', async () => {
    mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio({ jobs: [job()] }),
      'GET /jobs/job_1': job({
        status: 'failed',
        error_code: 'no_provider',
        error_message: 'No AI provider is connected for this story.',
      }),
    })
    renderInProject(<StudioPage />, route)
    const alert = await screen.findByText(/The draft was not written/)
    expect(alert.closest('.notice')).toHaveTextContent(
      'The draft was not written. No AI provider is connected for this story. Your text is unchanged.',
    )
    expect(screen.getByRole('link', { name: 'Connect an AI key' })).toBeInTheDocument()
  })

  it('lets the author read each finished draft, keep one, or discard one', async () => {
    let state = studio({ jobs: [job()] })
    const finished = studio({
      drafts: [
        draft(),
        draft({ id: 'drf_2', number: 2, content: DRAFT_TWO, words: 7, checks: [{ code: 'place_missing', key: 'k_place', severity: 'warning', message: 'Kestrel Point is never mentioned.', came_back: false }] }),
      ],
      jobs: [job({ status: 'succeeded', progress: 100, result: { drafts: 2 } })],
    })
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': () => state,
      'GET /jobs/job_1': () => {
        state = finished
        return job({ status: 'succeeded', progress: 100, result: { drafts: 2 } })
      },
      'POST /drafts/drf_1/keep': () => {
        state = studio({
          version_id: 'ver_3',
          content: DRAFT_ONE,
          versions: [
            { id: 'ver_3', number: 3, words: 9, origin: 'assistant', draft_id: 'drf_1', created_at: '2026-10-17T10:02:00Z' },
            ...studio().versions,
          ],
          drafts: [finished.drafts[1]],
        })
        return state
      },
      'DELETE /drafts/drf_2': reply(204),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)

    expect(await screen.findByText('2 drafts are ready.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Read the first one' }))
    expect(screen.getByRole('heading', { name: 'Draft 1' })).toBeInTheDocument()
    expect(screen.getByLabelText('The text of Draft 1')).toHaveTextContent(DRAFT_ONE)
    expect(screen.getByRole('button', { name: 'Draft 1' })).toHaveAttribute('aria-pressed', 'true')

    // Each draft carries its own checks.
    await user.click(screen.getByRole('button', { name: 'Draft 2' }))
    expect(screen.getByRole('complementary', { name: 'Notes on Draft 2' })).toHaveTextContent('Kestrel Point is never mentioned.')
    await expectAccessible(container)

    await user.click(screen.getByRole('button', { name: 'Discard Draft 2' }))
    const confirm = screen.getByRole('dialog', { name: 'Discard Draft 2?' })
    expect(confirm).toHaveTextContent('cannot be brought back')
    expect(within(confirm).getByRole('button', { name: 'Keep it waiting' })).toHaveFocus()
    await user.click(within(confirm).getByRole('button', { name: 'Discard Draft 2' }))
    await waitFor(() => expect(api.find('DELETE', '/drafts/drf_2')).toHaveLength(1))

    await user.click(await screen.findByRole('button', { name: 'Draft 1' }))
    await user.click(screen.getByRole('button', { name: 'Keep Draft 1' }))
    await waitFor(() => expect(api.find('POST', '/drafts/drf_1/keep')).toHaveLength(1))
    expect(api.find('POST', '/drafts/drf_1/keep')[0].body).toEqual({ base_version_id: 'ver_2' })
    expect(await screen.findByText(/Draft 1 is now your text/)).toBeInTheDocument()
    expect(screen.getByText(/Your earlier text is still there as version 2/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /The text of/ })).toHaveValue(DRAFT_ONE)
  })

  it('asks the assistant to check the saved text and reports what it found', async () => {
    let state = studio({ assistant: [] })
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': () => state,
      'POST /plan/pln_s/studio/reviews': reply(202, job({ id: 'job_2', kind: 'scene_review', status: 'queued' })),
      'GET /jobs/job_2': () => {
        state = studio()
        return job({ id: 'job_2', kind: 'scene_review', status: 'succeeded', result: { findings: 1, dropped: 1 } })
      },
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    await user.click(await screen.findByRole('button', { name: 'Check with the assistant' }))
    await waitFor(() => expect(api.find('POST', '/studio/reviews')).toHaveLength(1))
    expect(api.find('POST', '/studio/reviews')[0].body).toEqual({
      draft_id: null,
      passes: ['viewpoint', 'continuity', 'plan', 'voice', 'clarity'],
    })
    expect(await screen.findByText('The assistant made 1 note.')).toBeInTheDocument()
    expect(screen.getByText(/1 note did not quote your words exactly, so it was left out/)).toBeInTheDocument()
    expect(await screen.findByText('Ines could not see who waited from the stairs.')).toBeInTheDocument()
  })

  it('lets the author choose what the assistant checks, each with its cost', async () => {
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio({ assistant: [] }),
      'POST /plan/pln_s/studio/reviews': reply(202, job({ id: 'job_3', kind: 'scene_review', status: 'queued' })),
      'GET /jobs/job_3': job({ id: 'job_3', kind: 'scene_review', status: 'queued', stage: '' }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)
    const choices = await screen.findByRole('group', { name: 'What the assistant checks' })
    // Every check is chosen until the author says otherwise, each with what it costs.
    for (const [, label] of PASSES) expect(within(choices).getByRole('checkbox', { name: new RegExp(label) })).toBeChecked()
    expect(await within(choices).findByText('about 2,100 tokens')).toBeInTheDocument()
    expect(screen.getByText('About 6,800 tokens for all five.')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(choices).getByRole('checkbox', { name: /Your voice/ }))
    await user.click(within(choices).getByRole('checkbox', { name: /Continuity with the story bible/ }))
    expect(screen.getByText('About 3,400 tokens for the 3 chosen.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Check with the assistant' }))
    await waitFor(() => expect(api.find('POST', '/studio/reviews')).toHaveLength(1))
    expect(api.find('POST', '/studio/reviews')[0].body).toEqual({
      draft_id: null,
      passes: ['viewpoint', 'plan', 'clarity'],
    })
    // The choice is remembered in this browser for the next check.
    expect(JSON.parse(window.localStorage.getItem('novel-engine.check-passes.prj_1') ?? 'null')).toEqual([
      'viewpoint',
      'plan',
      'clarity',
    ])
  })

  it('needs at least one thing to check', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio({ assistant: [] }) })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const choices = await screen.findByRole('group', { name: 'What the assistant checks' })
    for (const [, label] of PASSES) await user.click(within(choices).getByRole('checkbox', { name: new RegExp(label) }))
    expect(screen.getByRole('button', { name: 'Check with the assistant' })).toBeDisabled()
    expect(screen.getByText('Choose at least one thing for the assistant to check.')).toBeInTheDocument()
  })

  it('keeps notes on how the author writes, said to guide every draft', async () => {
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio(),
      'GET /voice': { notes: 'Past tense.' },
      'PUT /voice': (call: RecordedCall) => call.body,
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    await user.click(await screen.findByRole('button', { name: 'How you write' }))
    const dialog = screen.getByRole('dialog', { name: 'How you write' })
    expect(dialog).toHaveTextContent('The assistant reads these first, every time it drafts a scene.')
    const notes = await within(dialog).findByLabelText('Your notes')
    expect(notes).toHaveValue('Past tense.')
    await user.type(notes, ' Close third person.')
    await user.click(within(dialog).getByRole('button', { name: 'Save how you write' }))
    await waitFor(() => expect(api.find('PUT', '/voice')).toHaveLength(1))
    expect(api.find('PUT', '/voice')[0].body).toEqual({ notes: 'Past tense. Close third person.' })
    expect(await within(dialog).findByText(/Saved. The next draft will follow these notes./)).toBeInTheDocument()
  })

  describe('reading the scene into the story', () => {
    it('says a scene was never read, explains what reading does, and offers to read it', async () => {
      mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
      const { container } = renderInProject(<StudioPage />, route)
      await screen.findByRole('heading', { level: 1 })
      const section = storyBible()
      expect(section).toHaveTextContent('Not read into the story yet.')
      expect(section).toHaveTextContent(
        'The assistant reads your saved text, finds the people, places and facts it states, quotes the words for each, and suggests them for you to review. After the first time, it reads only the paragraphs that changed.',
      )
      expect(within(section).getByRole('button', { name: 'Update the story from this scene' })).toBeEnabled()
      await expectAccessible(container)
    })

    it('says when it was read and what came of it, and needs no reading while nothing has changed', async () => {
      mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio({
          reading: { version_id: 'ver_2', version_number: 2, read_at: readAt, passages: 1, proposals: ['mut_1'], discarded: 0, changed_since: false },
        }),
        'GET /mutations/mut_1': withOps('mut_1', 2),
      })
      const { container } = renderInProject(<StudioPage />, route)
      await screen.findByRole('heading', { level: 1 })
      const section = storyBible()
      expect(section).toHaveTextContent('Read into the story at version 2, 2 hours ago.')
      expect(await within(section).findByText(/It suggested 2 changes/)).toBeInTheDocument()
      expect(within(section).getByRole('link', { name: 'See what it suggested' })).toHaveAttribute('href', '/projects/prj_1/review/mut_1')
      expect(within(section).getByRole('button', { name: 'Update the story from this scene' })).toBeDisabled()
      expect(section).toHaveTextContent('Nothing has changed since it was last read.')
      await expectAccessible(container)
    })

    it('says the scene changed since it was read, and sends several suggestions to the queue', async () => {
      mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio({
          reading: { version_id: 'ver_1', version_number: 1, read_at: readAt, passages: 2, proposals: ['mut_1', 'mut_2'], discarded: 0, changed_since: true },
        }),
        'GET /mutations/mut_1': withOps('mut_1', 1),
        'GET /mutations/mut_2': withOps('mut_2', 2),
      })
      renderInProject(<StudioPage />, route)
      await screen.findByRole('heading', { level: 1 })
      const section = storyBible()
      expect(section).toHaveTextContent('The scene has changed since it was last read at version 1, 2 hours ago.')
      expect(await within(section).findByText(/It suggested 3 changes/)).toBeInTheDocument()
      expect(within(section).getByRole('link', { name: 'See them in the review queue' })).toHaveAttribute('href', '/projects/prj_1/review')
      expect(within(section).getByRole('button', { name: 'Update the story from this scene' })).toBeEnabled()
    })

    it('says plainly why it cannot read yet: nothing written, or words not saved', async () => {
      mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio({ version_id: null, content: '', words: 0, versions: [], checks: [], assistant: [], reading: { changed_since: false, discarded: 0, passages: 0, proposals: [] } }),
      })
      const { unmount } = renderInProject(<StudioPage />, route)
      await screen.findByRole('textbox', { name: /The text of/ })
      expect(within(storyBible()).getByRole('button', { name: 'Update the story from this scene' })).toBeDisabled()
      expect(storyBible()).toHaveTextContent('Save some words first, so there is something to read.')
      unmount()

      mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
      const user = userEvent.setup()
      renderInProject(<StudioPage />, route)
      const box = await screen.findByRole('textbox', { name: /The text of/ })
      const section = storyBible()
      expect(within(section).getByRole('button', { name: 'Update the story from this scene' })).toBeEnabled()
      await user.type(box, ' She turned.')
      const button = within(section).getByRole('button', { name: 'Update the story from this scene' })
      expect(button).toBeDisabled()
      expect(button).toHaveAccessibleDescription('Save first, so it reads what you have now.')
    })

    it('reads the scene as work in the background and says what it suggested', async () => {
      let state = studio()
      const api = mockApi({
        ...base,
        'GET /plan/pln_s/studio': () => state,
        'POST /plan/pln_s/studio/readings': reply(202, job({ id: 'job_3', kind: 'scene_read', status: 'queued', stage: '', progress: 0 })),
        'GET /jobs/job_3': () => {
          state = studio({
            reading: { version_id: 'ver_2', version_number: 2, read_at: readAt, passages: 1, proposals: ['mut_1'], discarded: 1, changed_since: false },
          })
          return job({ id: 'job_3', kind: 'scene_read', status: 'succeeded', progress: 100, result: { passages: 1, proposals: ['mut_1'], discarded: 1, guesses: 0 } })
        },
        'GET /mutations/mut_1': withOps('mut_1', 3),
      })
      const user = userEvent.setup()
      const { container } = renderInProject(<StudioPage />, route)
      await user.click(await screen.findByRole('button', { name: 'Update the story from this scene' }))
      await waitFor(() => expect(api.find('POST', '/studio/readings')).toHaveLength(1))

      const outcome = (await screen.findByText('3 changes suggested from this scene.')).closest('.notice') as HTMLElement
      expect(outcome).toHaveTextContent(
        '3 changes suggested from this scene. Nothing changes in your story until you review them. 1 thing it found could not be tied to the scene’s words, so it was left out.',
      )
      expect(within(outcome).getByRole('link', { name: 'Review them' })).toHaveAttribute('href', '/projects/prj_1/review/mut_1')
      expect(await within(storyBible()).findByText(/Read into the story at version 2/)).toBeInTheDocument()
      await expectAccessible(container)
    })

    it('says which threads the scene moves, counting those an earlier stopped reading found', async () => {
      let state = studio()
      mockApi({
        ...base,
        'GET /plan/pln_s/studio': () => state,
        'POST /plan/pln_s/studio/readings': reply(202, job({ id: 'job_4', kind: 'scene_read', status: 'queued', stage: '', progress: 0 })),
        'GET /jobs/job_4': () => {
          state = studio({
            reading: { version_id: 'ver_2', version_number: 2, read_at: readAt, passages: 1, proposals: ['mut_2'], discarded: 0, changed_since: false },
          })
          return job({
            id: 'job_4',
            kind: 'scene_read',
            status: 'succeeded',
            progress: 100,
            result: { passages: 1, proposals: ['mut_2'], discarded: 0, guesses: 0, thread_steps: 2, carried_steps: 1 },
          })
        },
        'GET /mutations/mut_2': withOps('mut_2', 1),
      })
      const user = userEvent.setup()
      renderInProject(<StudioPage />, route)
      await user.click(await screen.findByRole('button', { name: 'Update the story from this scene' }))
      const outcome = (await screen.findByText('1 change suggested from this scene.')).closest('.notice') as HTMLElement
      expect(outcome).toHaveTextContent(
        'Among them, the scene moves 2 threads of the plan along or settles them, one found by an earlier reading that stopped.',
      )
    })

    it('shows its progress with a way to stop, then says when there was nothing new', async () => {
      let polls = 0
      const api = mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio({ jobs: [job({ id: 'job_3', kind: 'scene_read', stage: 'Reading the scene', progress: 10 })] }),
        'GET /jobs/job_3': () => {
          polls += 1
          return polls < 2
            ? job({ id: 'job_3', kind: 'scene_read', stage: 'Reading the scene', progress: 10 })
            : job({ id: 'job_3', kind: 'scene_read', status: 'succeeded', progress: 100, result: { passages: 1, proposals: [], discarded: 0, guesses: 0 } })
        },
      })
      renderInProject(<StudioPage />, route)
      const progress = await screen.findByRole('region', { name: 'Work in progress' })
      expect(progress).toHaveTextContent('The assistant is reading the scene into your story. Reading the scene')
      expect(within(progress).getByRole('button', { name: 'Stop' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Update the story from this scene' })).toBeDisabled()
      expect(await screen.findByText('Nothing new to add from this scene.', {}, { timeout: 4000 })).toBeInTheDocument()
      expect(api.find('GET', '/jobs/job_3').length).toBeGreaterThanOrEqual(2)
    })

    it('says plainly why a reading failed', async () => {
      mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio({ jobs: [job({ id: 'job_3', kind: 'scene_read' })] }),
        'GET /jobs/job_3': job({
          id: 'job_3',
          kind: 'scene_read',
          status: 'failed',
          error_code: 'provider',
          error_message: 'The AI provider did not answer.',
        }),
      })
      renderInProject(<StudioPage />, route)
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('The scene was not read into your story. The AI provider did not answer. Your story is unchanged.')
      expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    })
  })

  describe('filling in the plan card from the prose', () => {
    const unplanned: PlanItem = { ...scene, summary: '', refs: [{ entity_id: 'ent_ines', role: 'point_of_view' }] }
    const planless = { ...base, 'GET /plan': { items: [unplanned], open: [], conditions: [] } }
    const planJob = (overrides: Partial<StudioJob> = {}) =>
      job({ id: 'job_4', kind: 'scene_plan', stage: 'The assistant is reading the scene', progress: 30, ...overrides })

    it('offers it where the plan card is, saying what is missing, and reports the suggestion to review', async () => {
      let polls = 0
      const api = mockApi({
        ...planless,
        'GET /plan/pln_s/studio': studio(),
        'POST /plan/pln_s/studio/plan-card': reply(202, planJob({ status: 'queued', stage: '', progress: 0 })),
        'GET /jobs/job_4': () => {
          polls += 1
          return polls < 2
            ? planJob()
            : planJob({ status: 'succeeded', progress: 100, result: { mutation_id: 'mut_9', summary_filled: true, refs_added: 2, dropped: 1 } })
        },
      })
      const user = userEvent.setup()
      const { container } = renderInProject(<StudioPage />, route)
      const offer = (await screen.findByText('No summary or place planned for this scene yet.')).closest('div') as HTMLElement
      expect(offer).toHaveTextContent(
        'No summary or place planned for this scene yet. The assistant can suggest them, and who is in the scene, from what you have written. Nothing changes in your plan until you review it.',
      )
      await expectAccessible(container)
      await user.click(within(offer).getByRole('button', { name: 'Fill in the plan card from the prose' }))
      await waitFor(() => expect(api.find('POST', '/studio/plan-card')).toHaveLength(1))

      const progress = await screen.findByRole('region', { name: 'Work in progress' })
      expect(progress).toHaveTextContent('The assistant is filling in the plan card from the scene.')
      expect(within(offer).getByRole('button', { name: 'Fill in the plan card from the prose' })).toBeDisabled()

      const outcome = (await screen.findByText(/plan card\./, {}, { timeout: 4000 })).closest('.notice') as HTMLElement
      expect(outcome).toHaveTextContent(
        'The assistant suggests a summary and 2 people or places for this scene’s plan card. Nothing changes in your plan until you review it. 1 suggestion could not be tied to the scene’s words, so it was left out.',
      )
      expect(within(outcome).getByRole('link', { name: 'Review it' })).toHaveAttribute('href', '/projects/prj_1/review/mut_9')
      // Not offered again while that suggestion waits to be reviewed.
      expect(screen.queryByRole('button', { name: 'Fill in the plan card from the prose' })).not.toBeInTheDocument()
      await expectAccessible(container)
    })

    it('says plainly when there was nothing to add, or why it failed', async () => {
      mockApi({
        ...planless,
        'GET /plan/pln_s/studio': studio({ jobs: [planJob()] }),
        'GET /jobs/job_4': planJob({ status: 'succeeded', progress: 100, result: { mutation_id: null, summary_filled: false, refs_added: 0, dropped: 0 } }),
      })
      const { unmount } = renderInProject(<StudioPage />, route)
      expect((await screen.findByText('Nothing to add to the plan card.')).closest('.notice')).toHaveTextContent(
        'Nothing to add to the plan card. The assistant suggests only what the scene’s words show, and never replaces what you planned. Your plan is unchanged.',
      )
      expect(screen.queryByRole('link', { name: 'Review it' })).not.toBeInTheDocument()
      unmount()

      mockApi({
        ...planless,
        'GET /plan/pln_s/studio': studio({ jobs: [planJob()] }),
        'GET /jobs/job_4': planJob({ status: 'failed', error_code: 'provider', error_message: 'The AI provider did not answer.' }),
      })
      renderInProject(<StudioPage />, route)
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('The plan card was not filled in. The AI provider did not answer. Your plan is unchanged.')
      expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    })

    it('is offered only for written words on an unfinished card, and waits for unsaved words', async () => {
      // A full card: nothing to offer.
      mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
      const first = renderInProject(<StudioPage />, route)
      await screen.findByText(/Seen by/)
      expect(screen.queryByRole('button', { name: 'Fill in the plan card from the prose' })).not.toBeInTheDocument()
      first.unmount()

      // Nothing written yet: nothing to read it from.
      mockApi({
        ...planless,
        'GET /plan/pln_s/studio': studio({ version_id: null, content: '', words: 0, versions: [], checks: [], assistant: [] }),
      })
      const second = renderInProject(<StudioPage />, route)
      await screen.findByText(/Seen by/)
      expect(screen.queryByRole('button', { name: 'Fill in the plan card from the prose' })).not.toBeInTheDocument()
      second.unmount()

      // Unsaved words: save first, so it reads them.
      mockApi({ ...planless, 'GET /plan/pln_s/studio': studio() })
      const user = userEvent.setup()
      const third = renderInProject(<StudioPage />, route)
      const box = await screen.findByRole('textbox', { name: /The text of/ })
      expect(await screen.findByRole('button', { name: 'Fill in the plan card from the prose' })).toBeEnabled()
      await user.type(box, ' She turned.')
      const button = screen.getByRole('button', { name: 'Fill in the plan card from the prose' })
      expect(button).toBeDisabled()
      expect(button).toHaveAccessibleDescription('Save first, so it reads what you have now.')
      third.unmount()

      // A reader sees the card but cannot ask for it.
      mockApi({ ...planless, 'GET /plan/pln_s/studio': studio() })
      renderInProject(<StudioPage />, { ...route, context: { access: VIEWER } })
      await screen.findByText(/Seen by/)
      expect(screen.queryByRole('button', { name: 'Fill in the plan card from the prose' })).not.toBeInTheDocument()
    })
  })

  describe('opened at a passage from the review', () => {
    it('selects the passage in the version it was read from', async () => {
      mockApi({ ...base, 'GET /plan/pln_s/studio': studio() })
      const { start, end } = at('Tobin Ashe')
      const { container } = renderInProject(<StudioPage />, { ...route, at: `plan/pln_s/write?at=${start}-${end}&version=ver_2` })
      const box = (await screen.findByRole('textbox', { name: /The text of/ })) as HTMLTextAreaElement
      expect(await screen.findByText('The words the change came from are selected in your text.')).toBeInTheDocument()
      expect(box.selectionStart).toBe(start)
      expect(box.selectionEnd).toBe(end)
      await expectAccessible(container)
    })

    it('finds the words of an older version where they are now, or says they are gone', async () => {
      const OLD = 'Tobin Ashe waited by the glass.'
      const api = mockApi({
        ...base,
        'GET /plan/pln_s/studio': studio(),
        'GET /texts/src_1': { content: OLD },
      })
      const { unmount } = renderInProject(<StudioPage />, { ...route, at: 'plan/pln_s/write?at=0-10&version=ver_1' })
      const box = (await screen.findByRole('textbox', { name: /The text of/ })) as HTMLTextAreaElement
      expect(await screen.findByText('The words the change came from are selected in your text.')).toBeInTheDocument()
      expect(api.find('GET', '/texts/src_1')[0].search.get('version_id')).toBe('ver_1')
      expect(box.selectionStart).toBe(at('Tobin Ashe').start)
      expect(box.selectionEnd).toBe(at('Tobin Ashe').end)
      unmount()

      mockApi({ ...base, 'GET /plan/pln_s/studio': studio(), 'GET /texts/src_1': { content: 'Mara left the harbour.' } })
      renderInProject(<StudioPage />, { ...route, at: 'plan/pln_s/write?at=0-4&version=ver_1' })
      expect(await screen.findByText(/The words the change came from are no longer in this scene/)).toHaveTextContent(
        'The words the change came from are no longer in this scene: it has changed since they were read. This is the scene as it is now.',
      )
    })
  })

  it('dismisses a check with a reason, lists it under Dismissed, and brings it back with Undo', async () => {
    const before = studio()
    const dismissal = { id: 'dis_1', reason: 'intended' as const, note: '', expires_at: null, created_at: '2026-10-17T10:00:00Z' }
    const after = studio({
      checks: before.checks.map((check) => (check.key === 'k_stock' ? { ...check, dismissal } : check)),
    })
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': before,
      'POST /plan/pln_s/studio/dismissals': after,
      'DELETE /plan/pln_s/studio/dismissals/dis_1': before,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)
    const checks = (await screen.findByRole('heading', { name: 'Checks' })).closest('section') as HTMLElement

    await user.click(within(checks).getByRole('button', { name: 'Dismiss: A phrase readers often meet: “the weight of”.' }))
    const form = within(checks).getByRole('group', { name: 'Dismiss: A phrase readers often meet: “the weight of”.' })
    // The common answer is chosen already; how long defaults to "until the words change".
    expect(within(form).getByRole('button', { name: 'Intended' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(form).getByLabelText('How long')).toHaveValue('')
    expect(form).toHaveTextContent('It comes back by itself if the words it points at, or what it says, change.')
    await expectAccessible(container)

    // Their own words are needed for "Other reason", and what was typed is kept.
    await user.click(within(form).getByRole('button', { name: 'Other reason' }))
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('Say in a few words why this is not a problem.')
    expect(api.find('POST', '/plan/pln_s/studio/dismissals')).toHaveLength(0)
    await user.click(within(form).getByRole('button', { name: 'Intended' }))
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(api.find('POST', '/plan/pln_s/studio/dismissals')[0]?.body).toEqual({
        key: 'k_stock',
        reason: 'intended',
        note: '',
        days: null,
      }),
    )

    // It leaves the list, says so with an Undo, and waits under Dismissed with its reason.
    expect(await within(checks).findByText(/Dismissed as/)).toHaveTextContent('Dismissed as Intended.')
    expect(within(checks).getAllByRole('listitem').filter((item) => !item.closest('details'))).toHaveLength(1)
    const dismissed = within(checks).getByText('Dismissed: 1 check').closest('details') as HTMLElement
    expect(dismissed).toHaveTextContent('Intended · until the words change')
    await expectAccessible(container)
    await user.click(within(dismissed).getByRole('button', { name: 'Undo dismissing: A phrase readers often meet: “the weight of”.' }))
    await waitFor(() => expect(api.find('DELETE', '/plan/pln_s/studio/dismissals/dis_1')).toHaveLength(1))
    await waitFor(() => expect(within(checks).queryByText(/Dismissed:/)).not.toBeInTheDocument())
  })

  it('dismisses a kind of finding wherever it is about someone, and undoes it everywhere', async () => {
    const before = studio({
      checks: [
        {
          code: 'appearance_differs',
          key: 'k_eyes',
          severity: 'note',
          message: 'Here Ines Varga has “blue eyes”, but the story bible says: “Grey eyes.”',
          ...at('The weight of'),
          came_back: false,
          about: [{ entity_id: 'ent_ines', name: 'Ines Varga' }],
        },
      ],
    })
    const dismissal = {
      id: 'sd_1',
      reason: 'not_a_problem' as const,
      note: '',
      expires_at: null,
      created_at: '2026-10-17T10:00:00Z',
      scope_id: 'sd_1',
      applies_to: 'Eyes, hair or a beard unlike the accepted details, wherever it is about Ines Varga',
    }
    const after = studio({ checks: before.checks.map((check) => ({ ...check, dismissal })) })
    // A wider dismissal reaches other scenes, so the studio is read again after it.
    let now = before
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': () => now,
      'POST /plan/pln_s/studio/dismissals': () => (now = after),
      'DELETE /plan/pln_s/studio/dismissals/sd_1': () => (now = before),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)
    const checks = (await screen.findByRole('heading', { name: 'Checks' })).closest('section') as HTMLElement
    await user.click(within(checks).getByRole('button', { name: /^Dismiss: Here Ines Varga has/ }))
    const form = within(checks).getByRole('group', { name: /^Dismiss: Here Ines Varga has/ })
    const where = within(form).getByLabelText('Applies to')
    expect(where).toHaveValue('scene')
    await user.selectOptions(where, 'character:ent_ines')
    expect(form).toHaveTextContent('Every finding of this kind there is dismissed, including any found later, until you undo it.')
    expect(within(form).getByRole('option', { name: 'Until you undo it' })).toBeInTheDocument()
    await expectAccessible(container)
    await user.click(within(form).getByRole('button', { name: 'Not a problem' }))
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(api.find('POST', '/plan/pln_s/studio/dismissals')[0]?.body).toEqual({
        key: 'k_eyes',
        reason: 'not_a_problem',
        note: '',
        days: null,
        applies: 'character',
        entity_id: 'ent_ines',
      }),
    )
    expect(await within(checks).findByText(/Dismissed as/)).toHaveTextContent(
      'Applies to: Eyes, hair or a beard unlike the accepted details, wherever it is about Ines Varga.',
    )
    const dismissed = within(checks).getByText('Dismissed: 1 check').closest('details') as HTMLElement
    expect(dismissed).toHaveTextContent('Not a problem · until you undo it')
    await user.click(within(dismissed).getByRole('button', { name: /^Undo everywhere dismissing:/ }))
    await waitFor(() => expect(api.find('DELETE', '/plan/pln_s/studio/dismissals/sd_1')).toHaveLength(1))
  })

  it('dismisses a kind of finding for a stretch of scenes', async () => {
    const before = studio()
    const later = { ...scene, id: 'pln_t', title: 'The tide turns', position: 2 }
    const api = mockApi({
      ...base,
      'GET /plan': { items: [scene, later], open: [], conditions: [] },
      'GET /plan/pln_s/studio': before,
      'POST /plan/pln_s/studio/dismissals': before,
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const checks = (await screen.findByRole('heading', { name: 'Checks' })).closest('section') as HTMLElement
    await user.click(within(checks).getByRole('button', { name: 'Dismiss: A phrase readers often meet: “the weight of”.' }))
    const form = within(checks).getByRole('group', { name: 'Dismiss: A phrase readers often meet: “the weight of”.' })
    await user.selectOptions(within(form).getByLabelText('Applies to'), 'choose')
    await user.selectOptions(within(form).getByLabelText('To'), 'pln_t')
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(api.find('POST', '/plan/pln_s/studio/dismissals')[0]?.body).toMatchObject({
        applies: 'range',
        from_plan_id: 'pln_s',
        to_plan_id: 'pln_t',
      }),
    )
  })

  it('dismisses an assistant’s note in the author’s own words, for a while', async () => {
    const before = studio()
    const dismissal = {
      id: 'dis_2',
      reason: 'other' as const,
      note: 'She hears him first, a line later.',
      expires_at: '2026-10-24T10:00:00Z',
      created_at: '2026-10-17T10:00:00Z',
    }
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': before,
      'POST /plan/pln_s/studio/dismissals': studio({ assistant: before.assistant.map((note) => ({ ...note, dismissal })) }),
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const notes = (await screen.findByRole('heading', { name: 'The assistant’s notes' })).closest('section') as HTMLElement
    await user.click(within(notes).getByRole('button', { name: /^Dismiss: Ines could not see/ }))
    await user.click(within(notes).getByRole('button', { name: 'Other reason' }))
    await user.type(within(notes).getByLabelText('Your reason'), 'She hears him first, a line later.')
    await user.selectOptions(within(notes).getByLabelText('How long'), '7')
    await user.click(within(notes).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(api.find('POST', '/plan/pln_s/studio/dismissals')[0]?.body).toEqual({
        key: 'k_note',
        reason: 'other',
        note: 'She hears him first, a line later.',
        days: 7,
      }),
    )
    const dismissed = (await within(notes).findByText('Dismissed: 1 note')).closest('details') as HTMLElement
    expect(dismissed).toHaveTextContent('“She hears him first, a line later.”')
    expect(dismissed).toHaveTextContent('or until the words change')
  })

  it('lets a reader read the scene but change nothing', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio({ drafts: [draft()] }) })
    renderInProject(<StudioPage />, { ...route, context: { access: VIEWER } })
    const box = await screen.findByRole('textbox', { name: /The text of/ })
    expect(box).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: /Save|Draft with|Check with|Keep|Discard|Update the story|Dismiss/ })).not.toBeInTheDocument()
  })
})

describe('Naming an entry while writing', () => {
  it('offers entries by name and alias after @, and writes the chosen one as a mark', async () => {
    mockApi({
      ...base,
      'GET /entities': {
        items: [
          entity({ id: 'ent_ines', name: 'Ines Varga', aliases: ['the Keeper'] }),
          entity({ id: 'ent_point', type: 'Location', name: 'Kestrel Point', aliases: [] }),
          entity({ id: 'ent_mill', type: 'Location', name: 'Mill', aliases: [] }),
        ],
        limit: 200,
        offset: 0,
      },
      'GET /plan/pln_s/studio': studio({ content: '', version_id: null, versions: [], checks: [], assistant: [] }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)
    const box = (await screen.findByRole('textbox', { name: /The text of/ })) as HTMLTextAreaElement
    expect(screen.getByText(/Type @ to name an entry/)).toBeInTheDocument()

    await user.type(box, 'She climbed to @ke')
    const list = await screen.findByRole('listbox', { name: 'Entries in your story bible' })
    const options = within(list).getAllByRole('option')
    // A name that starts with it, then an alias that does.
    expect(options.map((option) => option.textContent)).toEqual(['Kestrel PointLocation', 'Ines VargaCharacter · as “the Keeper”'])
    expect(box).toHaveAttribute('aria-controls', list.id)
    expect(box).toHaveAttribute('aria-activedescendant', options[0].id)
    expect(options[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(/2 entries match/)).toBeInTheDocument()
    await expectAccessible(container)

    await user.keyboard('{ArrowDown}')
    expect(box).toHaveAttribute('aria-activedescendant', options[1].id)
    await user.keyboard('{ArrowDown}')
    expect(box).toHaveAttribute('aria-activedescendant', options[0].id)
    await user.keyboard('{Enter}')
    expect(box).toHaveValue('She climbed to @"Kestrel Point" ')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(box).not.toHaveAttribute('aria-activedescendant')
    expect(box.selectionStart).toBe(box.value.length)

    // Tab chooses too; a one-word name is written bare.
    await user.type(box, 'by the @mi')
    await screen.findByRole('listbox')
    await user.keyboard('{Tab}')
    expect(box).toHaveValue('She climbed to @"Kestrel Point" by the @Mill ')
    expect(box).toHaveFocus()

    // Escape closes the list and leaves the words as typed; it stays closed for that @.
    await user.type(box, '@In')
    await screen.findByRole('listbox')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(box).toHaveValue('She climbed to @"Kestrel Point" by the @Mill @In')
    expect(screen.getByText('Not saved yet', { exact: false })).toBeInTheDocument()

    // A name nobody has is said so, without getting in the way of Enter.
    await user.type(box, ' and @Zed')
    expect(await screen.findAllByText(/No entry is called “Zed”/)).toHaveLength(2)
    await user.keyboard('{Enter}')
    expect(box.value.endsWith('@Zed\n')).toBe(true)
  })

  it('chooses with the mouse', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio({ content: '', version_id: null, versions: [] }) })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    const box = await screen.findByRole('textbox', { name: /The text of/ })
    await user.type(box, '@')
    // Nothing typed yet: every entry, the most central first.
    await user.click(await screen.findByRole('option', { name: /Kestrel Point/ }))
    expect(box).toHaveValue('@"Kestrel Point" ')
  })
})

describe('Pictures on a scene', () => {
  const sceneLink = (overrides: Record<string, unknown> = {}) => ({
    id: 'lnk_s1',
    asset_id: 'ast_1',
    asset_version_id: 'asv_1',
    role: 'DEPICTS',
    target_record_type: 'plan_item',
    target_record_id: 'pln_s',
    created_revision: 4,
    retired_revision: null,
    ...overrides,
  })
  const picture = (overrides: Record<string, unknown> = {}) => ({
    asset_id: 'ast_1',
    version_id: 'asv_1',
    name: 'The lamp at night',
    content_type: 'image/png',
    byte_length: 4,
    created_at: '2026-10-17T09:00:00Z',
    shown: 1,
    ...overrides,
  })

  it('shows the scene’s pictures small in the head, and chooses more from the library', async () => {
    let n = 0
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:picture-${(n += 1)}`) }))
    let linked = false
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio(),
      'GET /links': () => ({
        items: linked ? [sceneLink(), sceneLink({ id: 'lnk_s2', asset_id: 'ast_2', asset_version_id: 'asv_2', created_revision: 5 })] : [sceneLink()],
      }),
      'GET /pictures': {
        items: [picture(), picture({ asset_id: 'ast_2', version_id: 'asv_2', name: 'The chart room', shown: 0 })],
      },
      'GET /content': new Blob(['x']),
      'POST /assets/ast_2/links': () => {
        linked = true
        return reply(201, sceneLink({ id: 'lnk_s2', asset_id: 'ast_2', asset_version_id: 'asv_2' }))
      },
      'POST /links/lnk_s1/retire': {},
      'POST /assets/ast_1/links': reply(201, sceneLink({ id: 'lnk_s3' })),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<StudioPage />, route)

    const strip = await screen.findByRole('list', { name: 'Pictures for “The lamp room at night”' })
    expect(await within(strip).findByRole('img', { name: 'The lamp at night' })).toHaveClass('scene-thumb')
    const toggle = screen.getByRole('button', { name: 'Change pictures' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    const panel = screen.getByRole('region', { name: 'Pictures for this scene' })
    expect(within(panel).getByText('Not shown anywhere')).toBeInTheDocument()
    // The picture already on the scene is not offered again.
    expect(within(panel).queryByRole('button', { name: 'Use “The lamp at night” for “The lamp room at night”' })).not.toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(panel).getByRole('button', { name: 'Use “The chart room” for “The lamp room at night”' }))
    await waitFor(() => expect(api.find('POST', '/assets/ast_2/links')).toHaveLength(1))
    expect(api.find('POST', '/assets/ast_2/links')[0].body).toEqual({
      target_id: 'pln_s',
      target_record_type: 'plan_item',
      asset_version_id: 'asv_2',
      role: 'DEPICTS',
      expected_revision: 3,
    })
    expect(await within(panel).findByText(/is now shown on this scene/)).toHaveTextContent('“The chart room” is now shown on this scene.')

    // Removing is not asked about first; it can be undone.
    await user.click(within(panel).getByRole('button', { name: 'Remove “The lamp at night” from “The lamp room at night”' }))
    await waitFor(() => expect(api.find('POST', '/links/lnk_s1/retire')).toHaveLength(1))
    await user.click(await within(panel).findByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.find('POST', '/assets/ast_1/links')).toHaveLength(1))
    expect(api.find('POST', '/assets/ast_1/links')[0].body).toMatchObject({ target_id: 'pln_s', target_record_type: 'plan_item' })

    await user.click(within(panel).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Pictures for this scene' })).not.toBeInTheDocument()
  })

  it('uploads a new picture onto the scene, and shows pictures to a reader without a way to change them', async () => {
    const api = mockApi({
      ...base,
      'GET /plan/pln_s/studio': studio(),
      'POST /plan/pln_s/pictures': reply(201, { link: sceneLink(), version: { id: 'asv_1' } }),
    })
    const user = userEvent.setup()
    renderInProject(<StudioPage />, route)
    await user.click(await screen.findByRole('button', { name: 'Add a picture' }))
    const panel = screen.getByRole('region', { name: 'Pictures for this scene' })
    expect(within(panel).getByText(/None yet/)).toBeInTheDocument()
    expect(within(panel).getByText('No other pictures in your story yet.')).toBeInTheDocument()
    await user.upload(
      within(panel).getByLabelText('Choose a picture for “The lamp room at night”'),
      new File([new Uint8Array([137, 80, 78, 71])], 'Lamp room.png', { type: 'image/png' }),
    )
    await waitFor(() => expect(api.find('POST', '/plan/pln_s/pictures')).toHaveLength(1))
    const sent = api.find('POST', '/plan/pln_s/pictures')[0]
    expect(sent.search.get('expected_revision')).toBe('3')
    expect(sent.search.get('name')).toBe('Lamp room')
  })

  it('shows a reader the scene’s pictures and nothing to change them with', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio(), 'GET /links': { items: [sceneLink()] }, 'GET /content': new Blob(['x']) })
    renderInProject(<StudioPage />, { ...route, context: { access: VIEWER } })
    expect(await screen.findByRole('list', { name: 'Pictures for “The lamp room at night”' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /pictures|Add a picture/ })).not.toBeInTheDocument()
  })
})
