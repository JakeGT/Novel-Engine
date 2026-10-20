import { screen, waitFor, within } from '@testing-library/react'
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
  licence: 'CC-BY',
  attribution: 'Jo',
  created_at: '2026-10-09T10:00:00Z',
}

const observation = (overrides: Record<string, unknown> = {}) => ({
  id: 'obs_1',
  asset_version_id: 'asv_1',
  kind: 'appearance',
  text: 'She wears a long blue coat.',
  words: 'She wears a long blue coat.',
  corrected_text: null,
  subject_record_id: 'ent_mara',
  subject_name: 'Mara Venn',
  region: { x: 0.2, y: 0.1, width: 0.5, height: 0.8 },
  confidence: 0.8,
  model: 'vision-model',
  prompt_version: 'describe-picture/1',
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
  links: [
    {
      id: 'lnk_1',
      role: 'DEPICTS',
      role_words: 'a picture of it',
      target_record_type: 'entity',
      target_record_id: 'ent_mara',
      target_name: 'Mara Venn',
      target_kind: 'character',
      secret: false,
      known_by: [],
      known_by_names: [],
      applies_from: null,
    },
  ],
  observations: [
    observation({ id: 'obs_0', kind: 'caption', text: 'A woman on a quay.', words: 'A woman on a quay.', region: null, subject_record_id: null, subject_name: null }),
    observation(),
  ],
  context_choice: 'describe',
  context_choice_words: 'Use its description',
  description: '',
  revision: 7,
  ...overrides,
})

const routes = (shown: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  'GET /pictures/ast_1': shown,
  'GET /describe/estimate': { prompt_tokens: 1600, completion_tokens: 700, total_tokens: 2300, sees_pictures: true, reason: null },
  'GET /content': new Blob(['x']),
  'GET /entities': { items: [entity()], limit: 200, offset: 0 },
  'GET /timeline': { unit: 'year', placed: [], unplaced: [] },
  ...extra,
})

const open = () => renderInProject(<PicturePage />, { path: 'pictures/:assetId', at: 'pictures/ast_1' })

describe('PicturePage', () => {
  it('shows the picture, its details, where it is shown and what was seen, in plain words', async () => {
    mockApi(routes(picture()))
    const { container } = open()
    expect(await screen.findByRole('heading', { name: 'Mara on the quay' })).toBeInTheDocument()
    expect(screen.getByText('Version 1 · PNG · 200 KB · 1200 × 800 pixels')).toBeInTheDocument()
    expect(screen.getByText('By Jo · Licence: CC-BY')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Mara Venn' })).toHaveAttribute('href', '/projects/prj_1/entries/ent_mara')
    expect(screen.getByText(/Anyone in the story may know what it shows/)).toBeInTheDocument()
    // The cost is shown before anything is sent.
    expect(await screen.findByText(/About 2,300 tokens/)).toBeInTheDocument()
    expect(screen.getByText('2 suggestions waiting for you.')).toBeInTheDocument()
    expect(screen.getByText(/About Mara Venn · Sure \(80%\) · about one part/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Use its description/ })).toBeChecked()
    await expectAccessible(container)
  })

  it('describes the picture and follows the work until it is done', async () => {
    let jobAsked = 0
    const api = mockApi(
      routes(picture({ observations: [] }), {
        'POST /pictures/ast_1/describe': { id: 'job_9', kind: 'media_describe', status: 'queued', stage: '', progress: 0, cancel_requested: false, prompt_tokens: 0, completion_tokens: 0, result: {}, created_at: '2026-10-09T10:00:00Z' },
        'GET /jobs/job_9': () => {
          jobAsked += 1
          return { id: 'job_9', kind: 'media_describe', status: 'succeeded', stage: 'Done', progress: 100, cancel_requested: false, prompt_tokens: 900, completion_tokens: 100, result: { observations: 3 }, created_at: '2026-10-09T10:00:00Z' }
        },
      }),
    )
    const user = userEvent.setup()
    open()
    expect(await screen.findByText('Nothing has been described yet.')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Describe this picture' }))
    expect(await screen.findByText(/Described. 3 suggestions to review below./)).toBeInTheDocument()
    expect(api.find('POST', '/describe')).toHaveLength(1)
    expect(jobAsked).toBeGreaterThan(0)
    // The picture is read again for the new suggestions.
    await waitFor(() => expect(api.find('GET', '/pictures/ast_1').length).toBeGreaterThan(1))
  })

  it('says so when the assistant cannot see pictures, and sends nothing', async () => {
    const api = mockApi(
      routes(picture(), {
        'GET /describe/estimate': {
          prompt_tokens: 1600, completion_tokens: 700, total_tokens: 2300, sees_pictures: false,
          reason: 'The assistant cannot see pictures. This needs the picture itself, so nothing was sent.',
        },
      }),
    )
    open()
    expect(await screen.findByText(/The assistant you use cannot see pictures/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Describe this picture/ })).not.toBeInTheDocument()
    expect(api.find('POST', '/describe')).toHaveLength(0)
  })

  it('accepts, corrects and rejects, and offers an accepted detail for review', async () => {
    const api = mockApi(
      routes(picture({ observations: [observation(), observation({ id: 'obs_2', text: 'A man waits.', words: 'A man waits.', subject_record_id: null, subject_name: null, region: null })] }), {
        'POST /observations/obs_1/review': (call: { body: { decision: string } }) => observation({ state: call.body.decision === 'accept' ? 'accepted' : 'corrected' }),
        'POST /observations/obs_2/review': observation({ id: 'obs_2', state: 'rejected' }),
      }),
    )
    const user = userEvent.setup()
    open()
    const coat = (await screen.findByText('She wears a long blue coat.')).closest('li') as HTMLElement
    await user.click(within(coat).getByRole('button', { name: 'Correct' }))
    const words = within(coat).getByLabelText('Your words')
    await user.clear(words)
    await user.type(words, 'She wears a long green coat.')
    await user.click(within(coat).getByRole('button', { name: 'Save correction' }))
    await waitFor(() => expect(api.find('POST', '/observations/obs_1/review')).toHaveLength(1))
    expect(api.find('POST', '/observations/obs_1/review')[0].body).toEqual({
      decision: 'correct',
      corrected_text: 'She wears a long green coat.',
    })
    const man = screen.getByText('A man waits.').closest('li') as HTMLElement
    await user.click(within(man).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(api.find('POST', '/observations/obs_2/review')[0].body).toEqual({ decision: 'reject', corrected_text: null }))
  })

  it('offers an accepted detail as a suggestion for the review queue', async () => {
    const api = mockApi(
      routes(picture({ observations: [observation({ state: 'accepted' })] }), {
        'POST /observations/obs_1/propose': { observation: observation({ state: 'accepted', proposal_mutation_set_id: 'mut_1' }), mutation_id: 'mut_1' },
      }),
    )
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Suggest as a detail of Mara Venn' }))
    await waitFor(() => expect(api.find('POST', '/propose')).toHaveLength(1))
    expect(screen.getByText(/nothing in the story changes until you approve it/)).toBeInTheDocument()
  })

  it('shows where a note is on the picture', async () => {
    mockApi(routes(picture()))
    const user = userEvent.setup()
    const { container } = open()
    const coat = (await screen.findByText('She wears a long blue coat.')).closest('li') as HTMLElement
    await screen.findByRole('img', { name: 'Mara on the quay' })
    await user.click(within(coat).getByRole('button', { name: 'Show where' }))
    const mark = container.querySelector('.picture-mark') as HTMLElement
    expect(mark.style.left).toBe('20%')
    expect(mark.style.height).toBe('80%')
    expect(screen.getByText(/Marked: where “She wears a long blue coat.” shows./)).toBeInTheDocument()
  })

  it('saves how the assistant is given the picture', async () => {
    const api = mockApi(
      routes(picture(), { 'PUT /pictures/ast_1/context': { asset_id: 'ast_1', choice: 'send', words: 'Send the picture' } }),
    )
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('radio', { name: /Send the picture/ }))
    await waitFor(() => expect(api.find('PUT', '/context')[0].body).toEqual({ choice: 'send' }))
    expect(await screen.findByText('Saved: send the picture.')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Send the picture/ })).toBeChecked()
    expect(screen.getByText(/no accepted description yet/)).toBeInTheDocument()
  })

  it('keeps what it shows secret to the characters who know it', async () => {
    const mara = entity()
    const tobin = { ...entity(), id: 'ent_tobin', name: 'Tobin Ashe' }
    const api = mockApi(
      routes(picture(), {
        'GET /entities': { items: [mara, tobin], limit: 200, offset: 0 },
        'PUT /pictures/ast_1/links/lnk_1': picture({
          links: [{ ...picture().links[0], id: 'lnk_2', secret: true, known_by: ['ent_tobin'], known_by_names: ['Tobin Ashe'] }],
        }),
      }),
    )
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Change who knows and when' }))
    await user.click(screen.getByRole('checkbox', { name: 'Keep what it shows secret' }))
    await user.click(screen.getByRole('checkbox', { name: 'Tobin Ashe' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.find('PUT', '/links/lnk_1')).toHaveLength(1))
    expect(api.find('PUT', '/links/lnk_1')[0].body).toEqual({
      secret: true,
      known_by: ['ent_tobin'],
      applies_from: null,
      expected_revision: 7,
    })
    expect(await screen.findByText(/Secret: only Tobin Ashe knows what it shows./)).toBeInTheDocument()
  })

  it('lets a reader look but not change anything', async () => {
    mockApi(routes(picture()))
    renderInProject(<PicturePage />, { path: 'pictures/:assetId', at: 'pictures/ast_1', context: { access: VIEWER } })
    expect(await screen.findByText('She wears a long blue coat.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Describe/ })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Send the picture/ })).toBeDisabled()
  })

  it('says plainly when the picture cannot be opened', async () => {
    mockApi({ 'GET /pictures/ast_1': problem(404, 'not_found', 'That picture was not found.') })
    open()
    expect(await screen.findByText(/That picture was not found/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to media' })).toBeInTheDocument()
  })
})
