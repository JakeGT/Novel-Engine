import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem, SceneDraft, Studio, StudioJob } from '../api/client'
import { entity } from '../test/fixtures'
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
      { code: 'stock_phrase', severity: 'note', message: 'A phrase readers often meet: “the weight of”.', ...at('The weight of') },
      { code: 'new_name', severity: 'warning', message: 'Tobin Ashe is not in your story bible yet.', ...at('Tobin Ashe') },
    ],
    assistant: [
      {
        id: 'fnd_1',
        category: 'viewpoint',
        message: 'Ines could not see who waited from the stairs.',
        suggestion: 'Let her hear him first.',
        ...at('waited by the glass'),
      },
    ],
    drafts: [],
    jobs: [],
    ...overrides,
  }
}

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
        draft({ id: 'drf_2', number: 2, content: DRAFT_TWO, words: 7, checks: [{ code: 'place_missing', severity: 'warning', message: 'Kestrel Point is never mentioned.' }] }),
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

  it('lets a reader read the scene but change nothing', async () => {
    mockApi({ ...base, 'GET /plan/pln_s/studio': studio({ drafts: [draft()] }) })
    renderInProject(<StudioPage />, { ...route, context: { access: VIEWER } })
    const box = await screen.findByRole('textbox', { name: /The text of/ })
    expect(box).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: /Save|Draft with|Check with|Keep|Discard/ })).not.toBeInTheDocument()
  })
})
