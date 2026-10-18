import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SourceTextDetail } from '../api/client'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { TextPage } from './TextPage'
import { TextsPage } from './TextsPage'

afterEach(() => vi.unstubAllGlobals())

const mara = entity({ id: 'ent_mara', name: 'Mara', aliases: [] })
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [] })
const CONTENT = '# The quay\n\nMara tied up at dawn. The Fox was waiting, and @Roe with her.\n\n## Later\n\nThe Fox slept.\n'
const span = (phrase: string, from = 0) => {
  const start = CONTENT.indexOf(phrase, from)
  return { start, end: start + phrase.length }
}

function detail(overrides: Partial<SourceTextDetail> = {}): SourceTextDetail {
  const version = { id: 'srv_1', number: 1, created_at: '2026-10-11T09:00:00Z', length: CONTENT.length }
  return {
    text: { id: 'src_1', title: 'Chapter one', kind: 'paste', created_at: '2026-10-11T09:00:00Z', versions: 1, latest_version_id: 'srv_1', length: CONTENT.length },
    version,
    versions: [version],
    content: CONTENT,
    sections: [
      { index: 0, title: 'The quay', start: 0, end: CONTENT.indexOf('## Later') },
      { index: 1, title: 'Later', start: CONTENT.indexOf('## Later'), end: CONTENT.length },
    ],
    mentions: [
      { ...span('Mara'), phrase: 'Mara', marked: false, status: 'known', entity_ids: ['ent_mara'] },
      { ...span('@Roe'), phrase: 'Roe', marked: true, status: 'unknown', entity_ids: [] },
    ],
    questions: [
      { phrase: 'Roe', count: 1, first_start: span('@Roe').start, first_end: span('@Roe').end, candidates: [], guessed: false },
      { phrase: 'The Fox', count: 2, first_start: span('The Fox').start, first_end: span('The Fox').end, candidates: ['ent_mara'], guessed: true },
    ],
    names: { ent_mara: 'Mara' },
    ...overrides,
  }
}

const base = {
  'GET /entities': { items: [mara, tobin], limit: 200, offset: 0 },
  'GET /vocabulary': VOCABULARY,
  'GET /ai/usage': reply(404),
}
const at = { path: 'texts/:sourceId', at: 'texts/src_1' }

describe('TextsPage', () => {
  it('brings in a pasted text and opens it', async () => {
    const api = mockApi({
      'GET /texts': { items: [] },
      'POST /texts': reply(201, detail().text),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<TextsPage />, { path: 'texts', at: 'texts' })

    expect(await screen.findByRole('heading', { name: 'Nothing brought in yet' })).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Bring in a text' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Bring in a text' })
    const send = within(dialog).getByRole('button', { name: 'Bring it in' })
    expect(send).toBeDisabled()
    await user.type(within(dialog).getByLabelText(/^What to call it/), 'Chapter one')
    await user.type(within(dialog).getByLabelText(/^The text/), 'Mara tied up at dawn.')
    expect(dialog).toHaveTextContent('5 words. Kept exactly as it is here.')
    await expectAccessible(container)
    await user.click(send)

    await waitFor(() => expect(api.find('POST', '/texts')).toHaveLength(1))
    expect(api.find('POST', '/texts')[0].body).toEqual({
      title: 'Chapter one',
      content: 'Mara tied up at dawn.',
      kind: 'paste',
    })
    await waitFor(() =>
      expect(screen.getAllByTestId('location')[0]).toHaveTextContent('/projects/prj_1/texts/src_1'),
    )
  }, 20_000)
})

describe('TextPage', () => {
  it('shows the words as written with the names in them, and what is named', async () => {
    mockApi({ ...base, 'GET /texts/src_1': detail() })
    const { container } = renderInProject(<TextPage />, at)

    const page = await screen.findByRole('article', { name: 'The text' })
    // Not a word is changed, marks and all.
    const written = [...page.querySelectorAll('.text-words')].map((node) => node.textContent).join('')
    expect(written).toBe(CONTENT)
    expect(within(page).getByRole('link', { name: 'Mara' })).toHaveAttribute('href', '/projects/prj_1/entries/ent_mara')
    expect(within(page).getByText('@Roe')).toHaveAttribute('title', 'Not in your story bible yet')

    const named = screen.getByRole('heading', { name: 'Named here' }).closest('section') as HTMLElement
    expect(named).toHaveTextContent('Mara Character · once')
    expect(screen.getByRole('button', { name: 'Have the assistant read The quay' })).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('asks who someone is one phrase at a time and remembers the answer', async () => {
    let current = detail()
    const api = mockApi({
      ...base,
      'GET /texts/src_1': () => current,
      'POST /texts/src_1/entries': () => {
        current = detail({ questions: detail().questions.slice(1) })
        return reply(201, { id: 'ent_roe', name: 'Roe', type: 'Character', revision: 4 })
      },
      'POST /names': () => {
        current = detail({ questions: [] })
        return reply(201, { id: 'alb_1', phrase: 'The Fox', verdict: 'is', entity_id: 'ent_mara', source_id: null, decided_at: '2026-10-11T09:00:00Z' })
      },
    })
    const user = userEvent.setup()
    renderInProject(<TextPage />, at)

    const card = (await screen.findByRole('heading', { name: 'Who is this?' })).closest('section') as HTMLElement
    expect(card).toHaveTextContent('“Roe” appears once')
    expect(card).toHaveTextContent('2 to answer')
    // Nothing answers to it: it can become an entry, pointing back at the text.
    await user.click(within(card).getByRole('button', { name: 'Add as a new entry' }))
    await waitFor(() => expect(api.find('POST', '/texts/src_1/entries')).toHaveLength(1))
    expect(api.find('POST', '/texts/src_1/entries')[0].body).toEqual({
      phrase: 'Roe',
      entity_type: 'Character',
      expected_revision: 3,
    })

    // The next one is the assistant's guess, and is only ever a question.
    const next = (await screen.findByText('“The Fox”')).closest('section') as HTMLElement
    expect(next).toHaveTextContent('The assistant thinks this means Mara.')
    expect(within(next).getByRole('button', { name: 'Not Mara' })).toBeInTheDocument()
    await user.click(within(next).getByRole('button', { name: 'It is Mara' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(1))
    // Held everywhere by default, with the words it was answered from kept as an example.
    expect(api.find('POST', '/names')[0].body).toEqual({
      phrase: 'The Fox',
      verdict: 'is',
      entity_id: 'ent_mara',
      source_id: null,
      what_if_only: false,
      from_scene_id: null,
      until_scene_id: null,
      viewpoint_id: null,
      example: { source_id: 'src_1', version_id: 'srv_1', ...span('The Fox') },
    })
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Who is this?' })).not.toBeInTheDocument())
  })

  it('keeps an answer to part of the story when asked: from this scene on, or when seen by someone', async () => {
    const scene = (id: string, title: string, position: number, pov?: string) => ({
      id,
      kind: 'scene' as const,
      title,
      summary: '',
      parent_id: null,
      position,
      refs: pov ? [{ entity_id: pov, role: 'point_of_view' }] : [],
      sets_up: [],
      pays_off: [],
      details: {},
      conditions: [],
      created_revision: 1,
    })
    const inScene = detail({
      text: { ...detail().text, kind: 'scene', scene_id: 'pln_2' },
      questions: detail().questions.slice(1),
    })
    const api = mockApi({
      ...base,
      'GET /plan': {
        items: [scene('pln_1', 'Dawn', 0), scene('pln_2', 'Noon', 1, 'ent_tobin'), scene('pln_3', 'Dusk', 2)],
        open: [],
        conditions: [],
      },
      'GET /texts/src_1': inScene,
      'POST /names': reply(201, { id: 'alb_1', phrase: 'The Fox', verdict: 'is', entity_id: 'ent_mara', source_id: null, decided_at: '2026-10-11T09:00:00Z' }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<TextPage />, at)

    const card = (await screen.findByRole('heading', { name: 'Who is this?' })).closest('section') as HTMLElement
    const where = within(card).getByLabelText('Where it holds')
    expect(where).toHaveValue('everywhere')
    await waitFor(() => expect(within(where).getByRole('option', { name: 'Only from this scene on' })).toBeInTheDocument())
    await user.selectOptions(where, 'from')
    // The scene the text belongs to is chosen already.
    expect(within(card).getByLabelText('From')).toHaveValue('pln_2')
    expect(within(card).getByRole('option', { name: '2. Noon (this scene)' })).toBeInTheDocument()
    await expectAccessible(container)
    await user.click(within(card).getByRole('button', { name: 'It is Mara' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(1))
    expect(api.find('POST', '/names')[0].body).toMatchObject({
      phrase: 'The Fox',
      from_scene_id: 'pln_2',
      until_scene_id: null,
      viewpoint_id: null,
      source_id: null,
    })

    // When seen by a character offers the scene's own viewpoint first; without one it asks.
    await user.selectOptions(where, 'seen_by')
    expect(within(card).getByLabelText('Seen by')).toHaveValue('ent_tobin')
    await user.selectOptions(within(card).getByLabelText('Seen by'), '')
    await user.click(within(card).getByRole('button', { name: 'It is Mara' }))
    expect(within(card).getByRole('alert')).toHaveTextContent('Choose whose eyes the scenes are seen through.')
    expect(api.find('POST', '/names')).toHaveLength(1)
    await user.selectOptions(within(card).getByLabelText('Seen by'), 'ent_tobin')
    await user.click(within(card).getByRole('button', { name: 'It is Mara' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(2))
    expect(api.find('POST', '/names')[1].body).toMatchObject({ viewpoint_id: 'ent_tobin', from_scene_id: null })
  })

  it('inside a what-if keeps the answer to it unless told otherwise', async () => {
    const api = mockApi({
      ...base,
      'GET /plan': { items: [], open: [], conditions: [] },
      'GET /texts/src_1': detail({ questions: detail().questions.slice(1) }),
      'POST /names': reply(201, { id: 'alb_1', phrase: 'The Fox', verdict: 'is', entity_id: 'ent_mara', source_id: null, decided_at: '2026-10-11T09:00:00Z' }),
    })
    const user = userEvent.setup()
    renderInProject(<TextPage />, { ...at, context: { scope: { workspaceId: 'ws_1', projectId: 'prj_1', branchId: 'br_1' } } })

    const card = (await screen.findByRole('heading', { name: 'Who is this?' })).closest('section') as HTMLElement
    const only = within(card).getByRole('checkbox', { name: 'Only in this what-if' })
    expect(only).toBeChecked()
    await user.click(within(card).getByRole('button', { name: 'It is Mara' }))
    await waitFor(() => expect(api.find('POST', '/names')).toHaveLength(1))
    expect(api.find('POST', '/names')[0].body).toMatchObject({ what_if_only: true })
  })

  it('has the assistant read a part and says plainly what came of it', async () => {
    const api = mockApi({
      ...base,
      'GET /texts/src_1': detail({ questions: [] }),
      'POST /assist/texts/src_1/read': {
        mutation_id: 'mut_9',
        operation_count: 4,
        validation_ok: true,
        start: 0,
        end: 60,
        more: true,
        implied: 1,
        guesses: 2,
        discarded: [{ kind: 'fact', reason: 'the passage does not say this in those words' }],
        missing: [{ name: 'CAPTAIN_OF', reason: 'Roe is captain of the Gull' }],
        limit: 24000,
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<TextPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Have the assistant read it' }))
    const result = await screen.findByRole('status', { name: 'What the assistant found' })
    expect(result).toHaveTextContent('The assistant suggests 4 changes from what it read.')
    expect(result).toHaveTextContent('Nothing is added until you have looked at them.')
    expect(result).toHaveTextContent('One of them is only implied by the text, not stated.')
    expect(result).toHaveTextContent('2 questions about who someone is')
    expect(result).toHaveTextContent('1 thing left out')
    expect(within(result).getByRole('link', { name: 'Look at them' })).toHaveAttribute('href', '/projects/prj_1/review/mut_9')
    expect(api.find('POST', '/read')[0].body).toEqual({ version_id: 'srv_1', start: 0, end: null, expected_revision: 3 })
    await expectAccessible(container)

    await user.click(within(result).getByRole('button', { name: 'Read the next part' }))
    await waitFor(() => expect(api.find('POST', '/read')).toHaveLength(2))
    expect(api.find('POST', '/read')[1].body).toMatchObject({ start: 60, end: null })
  })

  it('highlights the passage a change points back at', async () => {
    mockApi({ ...base, 'GET /texts/src_1': detail({ questions: [] }) })
    const place = span('tied up at dawn')
    Element.prototype.scrollIntoView = vi.fn()
    renderInProject(<TextPage />, { ...at, at: `texts/src_1#at-${place.start}-${place.end}` })
    const page = await screen.findByRole('article', { name: 'The text' })
    await waitFor(() => expect(page.querySelector('mark')).toHaveTextContent('tied up at dawn'))
  })

  it('lets someone who can only read see the text and nothing that changes it', async () => {
    mockApi({ ...base, 'GET /texts/src_1': detail() })
    renderInProject(<TextPage />, { ...at, context: { access: VIEWER, canWrite: false } })
    await screen.findByRole('article', { name: 'The text' })
    expect(screen.queryByRole('button', { name: 'Have the assistant read it' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add as a new entry' })).not.toBeInTheDocument()
  })

  it('offers entries after @ while changing the words; Escape closes the list, not the dialog', async () => {
    const api = mockApi({ ...base, 'GET /texts/src_1': detail(), 'POST /texts/src_1/versions': reply(201, detail().text) })
    const user = userEvent.setup()
    const { container } = renderInProject(<TextPage />, at)
    await screen.findByRole('article', { name: 'The text' })
    await user.click(screen.getByText('Change or remove'))
    await user.click(screen.getByRole('button', { name: 'Change the words' }))
    const dialog = screen.getByRole('dialog', { name: 'Change the words' })
    const box = within(dialog).getByLabelText(/^The text/) as HTMLTextAreaElement
    expect(dialog).toHaveTextContent('Type @ to name someone or something from your story bible.')

    await user.click(box)
    box.setSelectionRange(box.value.length, box.value.length)
    await user.type(box, 'Then @tob')
    expect(within(dialog).getByRole('option', { name: /Tobin Ashe/ })).toHaveAttribute('aria-selected', 'true')
    await expectAccessible(container)
    await user.keyboard('{Escape}')
    expect(within(dialog).queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Change the words' })).toBeInTheDocument()

    await user.type(box, 'i')
    await user.keyboard('{Enter}')
    expect(box.value.endsWith('Then @"Tobin Ashe" ')).toBe(true)
    // With the list closed, Escape closes the dialog as before.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Change the words' })).not.toBeInTheDocument()
    expect(api.find('POST', '/versions')).toHaveLength(0)
  })
})
