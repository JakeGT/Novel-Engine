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
      { code: 'stock_phrase', key: 'k_stock', severity: 'note', message: 'A phrase readers often meet: “the weight of”.', ...at('The weight of') },
      { code: 'new_name', key: 'k_name', severity: 'warning', message: 'Tobin Ashe is not in your story bible yet.', ...at('Tobin Ashe') },
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

const base = {
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
        draft({ id: 'drf_2', number: 2, content: DRAFT_TWO, words: 7, checks: [{ code: 'place_missing', key: 'k_place', severity: 'warning', message: 'Kestrel Point is never mentioned.' }] }),
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
    expect(api.find('POST', '/studio/reviews')[0].body).toEqual({ draft_id: null })
    expect(await screen.findByText('The assistant made 1 note.')).toBeInTheDocument()
    expect(screen.getByText(/1 note did not quote your words exactly, so it was left out/)).toBeInTheDocument()
    expect(await screen.findByText('Ines could not see who waited from the stairs.')).toBeInTheDocument()
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
    expect(form).toHaveTextContent('It comes back by itself if the words it points at change.')
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
