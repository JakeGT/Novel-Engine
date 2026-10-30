import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DraftCheck, SceneVoice } from '../api/client'
import { VIEWER, expectAccessible, mockApi, renderInProject } from '../test/harness'
import { Checks, Voice } from './StudioFindings'

afterEach(() => vi.unstubAllGlobals())

const TEXT = 'I walk to the harbor. The gravel crunches. Gravel, gravel, gravel everywhere.'

const voice: SceneVoice = {
  plan_id: 'pln_s4',
  title: 'Four',
  version_id: 'ver_4',
  version_number: 2,
  stats: {
    words: 820,
    sentences: 40,
    english: true,
    sentence_mean: 24.2,
    sentence_median: 22,
    sentence_long: 38,
    sentence_spread: 6.1,
    bands: [0, 2, 10, 18, 8, 2],
    dialogue_share: 0.05,
    adverb_rate: 4.1,
    adverbs: ['quietly'],
    echoes: [{ word: 'gravel', times: 4, start: TEXT.indexOf('gravel'), end: TEXT.length - 1 }],
    person: 'first',
    tense: 'present',
    past_share: 0.1,
  },
  usual: {
    enough: true,
    scenes: 3,
    words: 2100,
    own_only: true,
    drafts_left_out: 0,
    left_out: [],
    sentence_mean: 12.4,
    dialogue_share: 0.12,
    adverb_rate: 3.9,
    person: 'third',
    tense: 'past',
    needs_scenes: 3,
    needs_words: 2000,
  },
  notes: [
    { code: 'voice_person', message: 'This scene is told with “I” in the narration; your other scenes are told with “he”, “she” or “they”.', start: null, end: null },
  ],
  counted: true,
  left_out: false,
  not_compared: null,
  band_labels: ['1–5 words', '6–10 words', '11–20 words', '21–30 words', '31–40 words', '41 or more'],
  min_words: 300,
  measures_version: 'voice/1',
}

const point = (start?: number | null, end?: number | null) =>
  start == null || end == null ? undefined : { start, end }

describe('Voice', () => {
  it('compares the scene with the usual, notes how it departs, and can leave it out', async () => {
    const api = mockApi({
      'GET /plan/pln_s4/voice': voice,
      'PUT /plan/pln_s4/voice/usual': { ...voice, left_out: true, counted: false },
    })
    const user = userEvent.setup()
    const onShow = vi.fn()
    const { container } = renderInProject(
      <Voice planId="pln_s4" versionId="ver_4" text={TEXT} point={point} onShow={onShow} stale={false} canWrite />,
    )
    const panel = await screen.findByRole('region', { name: 'How it sounds' })
    expect(await within(panel).findByText(/told with “I” in the narration/)).toBeInTheDocument()
    expect(within(panel).getByText('Against your usual, from 3 other scenes you wrote yourself. No assistant needed.')).toBeInTheDocument()
    const table = within(panel).getByRole('table', { name: 'This scene and your usual' })
    expect(within(table).getByRole('row', { name: /Sentence length about 24 words about 12/ })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: /Told with “I” with “he” or “she”/ })).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Show in the text' }))
    expect(onShow).toHaveBeenCalledWith({ start: TEXT.indexOf('gravel'), end: TEXT.length - 1 })
    await expectAccessible(container)

    await user.click(within(panel).getByRole('checkbox', { name: /Count this scene in your usual/ }))
    await waitFor(() => expect(api.find('PUT', '/plan/pln_s4/voice/usual')).toHaveLength(1))
    expect(api.find('PUT', '/plan/pln_s4/voice/usual')[0].body).toEqual({ left_out: true })
    await waitFor(() => expect(within(panel).getByRole('checkbox', { name: /Count this scene/ })).not.toBeChecked())
  })

  it('dismisses a note on how it sounds, says who says what, and brings the note back', async () => {
    const note = {
      code: 'voice_speaker_short_forms',
      message: 'Tobin Ashe uses short forms such as “don\'t” and “I\'m” far less here.',
      start: null,
      end: null,
      key: 'k_voice',
    }
    const dismissal = { id: 'dis_v', reason: 'intended' as const, note: '', expires_at: null, created_at: '2026-10-21T09:00:00Z' }
    const speakers = [
      { name: 'Tobin Ashe', lines: 20, words: 600, sentence_mean: 30.4, short_forms: 0, elsewhere: 3, compared: true },
      { name: 'Mara', lines: 4, words: 20, sentence_mean: 5, short_forms: 0, elsewhere: 0, compared: false },
    ]
    const before = { ...voice, notes: [note], speakers, attributed: 0.92 }
    const after = { ...before, notes: [{ ...note, dismissal }] }
    const api = mockApi({
      'GET /plan/pln_s4/voice': before,
      'POST /plan/pln_s4/voice/dismissals': after,
      'DELETE /plan/pln_s4/voice/dismissals/dis_v': before,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(
      <Voice planId="pln_s4" versionId="ver_4" text={TEXT} point={point} onShow={vi.fn()} stale={false} canWrite />,
    )
    const panel = await screen.findByRole('region', { name: 'How it sounds' })
    await user.click(await within(panel).findByText('Who says what'))
    const who = within(panel).getByRole('table', { name: 'Each speaker in this scene' })
    expect(within(who).getByRole('rowheader', { name: /Tobin Ashe\s*Compared with 3 other scenes/ })).toBeInTheDocument()
    expect(within(who).getByRole('rowheader', { name: /Mara\s*Too little to compare yet/ })).toBeInTheDocument()
    expect(within(panel).getByText(/About 92% of the dialogue here is tied to who says it/)).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: /^Dismiss: Tobin Ashe uses short forms/ }))
    const form = within(panel).getByRole('group', { name: /^Dismiss: Tobin Ashe uses short forms/ })
    // Only this scene: no wider choice for a note on how it sounds.
    expect(within(form).queryByLabelText('Applies to')).not.toBeInTheDocument()
    await expectAccessible(container)
    await user.click(within(form).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(api.find('POST', '/plan/pln_s4/voice/dismissals')[0]?.body).toEqual({
        key: 'k_voice',
        reason: 'intended',
        note: '',
        days: null,
      }),
    )
    const dismissed = (await within(panel).findByText('Dismissed: 1 note on how it sounds')).closest('details') as HTMLElement
    expect(within(panel).getByText('Nothing else stands out.')).toBeInTheDocument()
    await user.click(within(dismissed).getByRole('button', { name: /^Undo dismissing: Tobin Ashe/ }))
    await waitFor(() => expect(api.find('DELETE', '/plan/pln_s4/voice/dismissals/dis_v')).toHaveLength(1))
  })

  it('says why nothing is compared, and offers no choice to a reader', async () => {
    mockApi({
      'GET /plan/pln_s4/voice': {
        ...voice,
        notes: [],
        usual: { ...voice.usual, enough: false, scenes: 1 },
        not_compared: 'Not enough of your writing yet to know your usual: it needs 3 scenes and 2,000 words of your own.',
      },
    })
    renderInProject(
      <Voice planId="pln_s4" versionId="ver_4" text={TEXT} point={point} onShow={vi.fn()} stale={false} canWrite={false} />,
      { context: { access: VIEWER } },
    )
    expect(await screen.findByText(/Not enough of your writing yet/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})

const check: DraftCheck = {
  code: 'stock_phrase',
  severity: 'note',
  came_back: false,
  message: '“a testament to” is stock phrasing that readers often notice.',
  start: 0,
  end: 6,
  key: 'chk_key_1',
  dismissal: null,
}

describe('Settling a finding in the story bible', () => {
  it('shows the cost first, drafts a change for review, and never touches the words', async () => {
    const api = mockApi({
      'POST /plan/pln_s4/findings/proposal-estimate': { total_tokens: 3400, has_passage: true },
      'POST /plan/pln_s4/findings/proposal': {
        mutation_id: 'mut_9',
        understood: 'Mara is no longer at Lantern Harbor',
        questions: [],
        notes: [],
        operation_count: 1,
      },
    })
    const user = userEvent.setup()
    const dismissals = {
      dismiss: { isPending: false, isSuccess: false, mutate: vi.fn(), error: null, variables: undefined },
      undo: { isPending: false, mutate: vi.fn(), error: null },
    } as never
    const { container } = renderInProject(
      <Checks checks={[check]} text={TEXT} point={point} onShow={vi.fn()} stale={false} empty={false} dismissals={dismissals} planId="pln_s4" />,
    )
    await user.click(screen.getByRole('button', { name: 'Settle it in the story bible…' }))
    const form = screen.getByRole('group', { name: 'Settle it in the story bible' })
    expect(within(form).getByText(/Your words are not changed/)).toBeInTheDocument()
    expect(await within(form).findByText(/About 3,400 tokens/)).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Change the story bible instead' }))
    await user.click(within(form).getByRole('button', { name: 'Draft the change' }))
    await waitFor(() => expect(api.find('POST', '/findings/proposal')).toHaveLength(1))
    expect(api.find('POST', '/findings/proposal')[0].body).toEqual({
      kind: 'check',
      key: 'chk_key_1',
      choice: 'change_bible',
    })
    expect(await within(form).findByRole('link', { name: 'Review the change' })).toHaveAttribute(
      'href',
      '/projects/prj_1/review/mut_9',
    )
    await expectAccessible(container)
  })
})
