import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, VIEWER } from '../test/harness'
import { PicturePage } from './PicturePage'

beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:picture') }))
})
afterEach(() => vi.unstubAllGlobals())

const version = {
  id: 'asv_1',
  version: 1,
  content_type: 'image/png',
  byte_length: 204800,
  source: null,
  licence: null,
  attribution: null,
  created_at: '2026-10-09T10:00:00Z',
}

const part = (overrides: Record<string, unknown> = {}) => ({
  id: 'seg_1',
  x: 600,
  y: 160,
  width: 120,
  height: 80,
  region: { x: 0.5, y: 0.2, width: 0.1, height: 0.1 },
  label: 'Mara’s scar',
  entity_id: 'ent_mara',
  entity_name: 'Mara Venn',
  ...overrides,
})

const seen = (overrides: Record<string, unknown> = {}) => ({
  id: 'obs_w',
  asset_version_id: 'asv_1',
  kind: 'text_in_picture',
  text: 'THE BLACK SWAN',
  words: 'THE BLACK SWAN',
  corrected_text: null,
  subject_record_id: null,
  subject_name: null,
  region: { x: 0.52, y: 0.22, width: 0.05, height: 0.05 },
  segment_id: 'seg_1',
  part_name: 'Mara’s scar',
  confidence: 0.9,
  model: 'vision-model',
  prompt_version: 'describe-picture/2',
  job_id: 'job_1',
  state: 'proposed',
  reviewed_at: null,
  proposal_mutation_set_id: null,
  created_at: '2026-10-09T10:00:00Z',
  current: true,
  ...overrides,
})

const picture = (overrides: Record<string, unknown> = {}) => ({
  asset_id: 'ast_1',
  name: 'Mara on the quay',
  version,
  versions: [version],
  width: 1200,
  height: 800,
  links: [],
  observations: [],
  parts: [],
  context_choice: 'describe',
  context_choice_words: 'Use its description',
  description: '',
  revision: 7,
  ...overrides,
})

const segment = (body: Record<string, unknown>) => ({ id: 'seg_9', kind: 'region', created_at: '2026-10-09T10:00:00Z', ...body })

const routes = (shown: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  'GET /pictures/ast_1': shown,
  'GET /describe/estimate': { prompt_tokens: 1600, completion_tokens: 700, total_tokens: 2300, sees_pictures: true, reason: null },
  'GET /content': new Blob(['x']),
  'GET /entities': { items: [entity()], limit: 200, offset: 0 },
  'GET /timeline': { unit: 'year', placed: [], unplaced: [] },
  ...extra,
})

const open = () => renderInProject(<PicturePage />, { path: 'pictures/:assetId', at: 'pictures/ast_1' })

describe('marking parts of a picture', () => {
  it('marks a part by typing where it is, with a name and the entry it shows', async () => {
    const api = mockApi(
      routes(picture(), { 'POST /segments': (call: { body: Record<string, unknown> }) => segment(call.body) }),
    )
    const user = userEvent.setup()
    const { container } = open()
    expect(await screen.findByText('No parts are marked yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mark a part' }))
    const form = screen.getByRole('form', { name: 'Mark a part' })
    // A first box is there to adjust: the middle quarter of the picture.
    expect(within(form).getByLabelText('From the left')).toHaveValue(450)
    expect(within(form).getByLabelText('Width')).toHaveValue(300)
    expect(within(form).getByText(/The picture is 1200 by 800 pixels/)).toBeInTheDocument()
    await user.type(within(form).getByLabelText('Name (optional)'), 'The scar')
    await user.selectOptions(within(form).getByLabelText('Who or what it shows (optional)'), 'Mara Venn')
    const width = within(form).getByLabelText('Width')
    await user.clear(width)
    await user.type(width, '120')
    await expectAccessible(container)
    await user.click(within(form).getByRole('button', { name: 'Mark this part' }))
    expect(await screen.findByText('Marked “The scar”.')).toBeInTheDocument()
    expect(api.find('POST', '/segments')[0].body).toEqual({
      x: 450,
      y: 300,
      width: 120,
      height: 200,
      label: 'The scar',
      entity_id: entity().id,
    })
    expect(api.find('POST', '/segments')[0].path).toContain('/assets/ast_1/versions/asv_1/segments')
  })

  it('keeps what was typed and says how to fix a part that runs off the picture', async () => {
    const api = mockApi(routes(picture()))
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Mark a part' }))
    const left = screen.getByLabelText('From the left')
    await user.clear(left)
    await user.type(left, '1150')
    await user.click(screen.getByRole('button', { name: 'Mark this part' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That goes past the picture’s edge. It is 1200 by 800 pixels.',
    )
    expect(left).toHaveValue(1150)
    expect(left).toHaveAttribute('aria-invalid', 'true')
    expect(api.find('POST', '/segments')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form', { name: 'Mark a part' })).not.toBeInTheDocument()
  })

  it('draws a part by dragging across the picture, filling in the numbers', async () => {
    mockApi(routes(picture()))
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Mark a part' }))
    const layer = await screen.findByTestId('picture-draw')
    layer.getBoundingClientRect = () => ({ left: 0, top: 0, width: 600, height: 400, right: 600, bottom: 400, x: 0, y: 0, toJSON: () => ({}) })
    fireEvent.pointerDown(layer, { clientX: 60, clientY: 40, pointerId: 1 })
    fireEvent.pointerMove(layer, { clientX: 150, clientY: 100, pointerId: 1 })
    fireEvent.pointerUp(layer, { clientX: 160, clientY: 120, pointerId: 1 })
    expect(screen.getByLabelText('From the left')).toHaveValue(120)
    expect(screen.getByLabelText('From the top')).toHaveValue(80)
    expect(screen.getByLabelText('Width')).toHaveValue(200)
    expect(screen.getByLabelText('Height')).toHaveValue(160)
    expect(screen.getByText('The part being marked is drawn on the picture.')).toBeInTheDocument()
  })

  it('lists marked parts, shows one, changes it, and removes it with a way back', async () => {
    const api = mockApi(
      routes(picture({ parts: [part()] }), {
        'PUT /segments/seg_1': (call: { body: Record<string, unknown> }) => segment({ ...call.body, id: 'seg_1' }),
        'POST /segments/seg_1/retire': segment({ ...part(), id: 'seg_1' }),
        'POST /segments': (call: { body: Record<string, unknown> }) => segment(call.body),
      }),
    )
    const user = userEvent.setup()
    const { container } = open()
    const item = (await screen.findByText('Mara’s scar')).closest('li') as HTMLElement
    expect(within(item).getByRole('link', { name: 'Mara Venn' })).toHaveAttribute('href', '/projects/prj_1/entries/ent_mara')
    expect(within(item).getByText(/120 by 80 pixels, 600 from the left and 160 from the top/)).toBeInTheDocument()
    await user.click(within(item).getByRole('button', { name: 'Show where' }))
    expect(screen.getByText('Marked: the part “Mara’s scar”.')).toBeInTheDocument()
    expect(within(item).getByRole('button', { name: 'Hide where' })).toHaveAttribute('aria-pressed', 'true')
    await expectAccessible(container)

    await user.click(within(item).getByRole('button', { name: 'Change' }))
    const form = screen.getByRole('form', { name: 'Change “Mara’s scar”' })
    expect(within(form).getByLabelText('From the left')).toHaveValue(600)
    const name = within(form).getByLabelText('Name (optional)')
    await user.clear(name)
    await user.type(name, 'Her scar')
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved the changes to “Her scar”.')).toBeInTheDocument()
    expect(api.find('PUT', '/segments/seg_1')[0].body).toMatchObject({ x: 600, label: 'Her scar', entity_id: 'ent_mara' })

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(await screen.findByText(/Removed “Mara’s scar”. Notes that pointed at it keep where it was./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByText('“Mara’s scar” is marked again.')).toBeInTheDocument()
    expect(api.find('POST', '/retire')).toHaveLength(1)
    expect(api.find('POST', '/segments')[0].body).toEqual({
      x: 600,
      y: 160,
      width: 120,
      height: 80,
      label: 'Mara’s scar',
      entity_id: 'ent_mara',
    })
  })

  it('says why a part could not be marked', async () => {
    mockApi(
      routes(picture(), {
        'POST /segments': problem(400, 'validation_error', 'That entry is not in the story. Choose another, or none.'),
      }),
    )
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Mark a part' }))
    await user.click(screen.getByRole('button', { name: 'Mark this part' }))
    expect(await screen.findByText(/That entry is not in the story/)).toBeInTheDocument()
    expect(screen.getByRole('form', { name: 'Mark a part' })).toBeInTheDocument()
  })

  it('waits for the picture to be ready, and lets a reader only look', async () => {
    mockApi(routes(picture({ width: null, height: null })))
    open()
    expect(await screen.findByText('Parts can be marked once the picture is ready.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark a part' })).not.toBeInTheDocument()
  })

  it('shows a reader the parts but no way to change them', async () => {
    mockApi(routes(picture({ parts: [part()] })))
    renderInProject(<PicturePage />, { path: 'pictures/:assetId', at: 'pictures/ast_1', context: { access: VIEWER } })
    expect(await screen.findByText('Mara’s scar')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show where' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark a part' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
  })
})

describe('writing read in a picture, and what its parts show', () => {
  it('shows writing for review, said to be read letter by letter, in its part', async () => {
    const api = mockApi(
      routes(
        picture({
          parts: [part()],
          observations: [
            seen(),
            seen({ id: 'obs_p', kind: 'caption', text: 'A thin pale scar.', words: 'A thin pale scar.', subject_record_id: 'ent_mara', subject_name: 'Mara Venn' }),
          ],
        }),
        { 'POST /observations/obs_w/review': seen({ state: 'corrected', corrected_text: 'The Black Swan', words: 'The Black Swan' }) },
      ),
    )
    const user = userEvent.setup()
    const { container } = open()
    const writing = (await screen.findByText('THE BLACK SWAN')).closest('li') as HTMLElement
    expect(within(writing).getByText('Writing in it')).toBeInTheDocument()
    expect(within(writing).getByText(/letter by letter/)).toBeInTheDocument()
    expect(within(writing).getByText(/Sure \(90%\) · in the part “Mara’s scar”/)).toBeInTheDocument()
    // Writing is never offered as a detail of how anyone looks.
    expect(within(writing).queryByRole('button', { name: /Suggest as a detail/ })).not.toBeInTheDocument()
    const caption = screen.getByText('A thin pale scar.').closest('li') as HTMLElement
    expect(within(caption).getByText('What a marked part shows')).toBeInTheDocument()
    await expectAccessible(container)
    await user.click(within(writing).getByRole('button', { name: 'Correct' }))
    const words = within(writing).getByLabelText('Your words')
    await user.clear(words)
    await user.type(words, 'The Black Swan')
    await user.click(within(writing).getByRole('button', { name: 'Save correction' }))
    await waitFor(() => expect(api.find('POST', '/observations/obs_w/review')).toHaveLength(1))
    expect(api.find('POST', '/observations/obs_w/review')[0].body).toEqual({
      decision: 'correct',
      corrected_text: 'The Black Swan',
    })
  })
})
