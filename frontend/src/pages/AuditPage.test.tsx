import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Audit, AuditEstimate, PlanItem, StudioJob } from '../api/client'
import { AuditDialog } from '../components/AuditDialog'
import { VIEWER, expectAccessible, mockApi, problem, renderInProject, reply } from '../test/harness'
import { AuditPage } from './AuditPage'

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const beat: PlanItem = {
  id: 'pln_b1',
  kind: 'beat',
  title: 'She finds the false chart',
  summary: '',
  parent_id: null,
  position: 0,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 1,
}

function job(overrides: Partial<StudioJob> = {}): StudioJob {
  return {
    id: 'job_a',
    kind: 'story_audit',
    status: 'succeeded',
    stage: '',
    progress: 100,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    prompt_tokens: 5200,
    completion_tokens: 300,
    cached_prompt_tokens: 0,
    automatic: false,
    result: {},
    created_at: '2026-10-18T10:00:00Z',
    finished_at: '2026-10-18T10:02:00Z',
    ...overrides,
  }
}

const passEstimate = (check_pass: 'viewpoint' | 'continuity' | 'plan' | 'voice' | 'clarity', label: string, total: number) => ({
  check_pass,
  label,
  description: `What ${label.toLowerCase()} looks at.`,
  prompt_tokens: total - 1200,
  completion_tokens: 1200,
  total_tokens: total,
})

const estimate: AuditEstimate = {
  plan_id: 'pln_b1',
  title: 'She finds the false chart',
  scenes: [
    { plan_id: 'pln_s1', title: 'The lamp room at night', written: true, words: 900, total_tokens: 7000 },
    { plan_id: 'pln_s2', title: 'The chart room', written: true, words: 700, total_tokens: 6000 },
    { plan_id: 'pln_s3', title: 'The father returns', written: false, words: 0, total_tokens: 0 },
  ],
  written: 2,
  max_scenes: 60,
  passes: [
    passEstimate('viewpoint', 'Viewpoint and secrets', 3000),
    passEstimate('continuity', 'Continuity with the story bible', 4000),
    passEstimate('plan', 'What the plan asks for', 2000),
    passEstimate('voice', 'Your voice', 2400),
    passEstimate('clarity', 'Clarity', 1600),
  ],
  total_tokens: 13000,
}

const TEXT = 'Ines climbed to the lamp room. Her father had drawn the false chart.'

const report: Audit = {
  job: job(),
  plan_id: 'pln_b1',
  title: 'She finds the false chart',
  passes: ['viewpoint', 'clarity'],
  scenes: [
    {
      plan_id: 'pln_s1',
      title: 'The lamp room at night',
      in_plan: true,
      version_id: 'ver_3',
      version_number: 3,
      current: false,
      status: 'checked',
      checks: [
        {
          code: 'stock_phrase',
          severity: 'note',
          came_back: false,
          message: '“the weight of” is stock phrasing that readers often notice.',
          start: null,
          end: null,
          key: '',
          quoted: '',
        },
      ],
      story_checks: ['This scene shows the reader something before it is set up.'],
      notes: [
        {
          id: 'fnd_1',
          category: 'viewpoint',
          check_pass: 'viewpoint',
          message: 'Ines cannot know her father drew it yet.',
          suggestion: 'Let her suspect it.',
          start: TEXT.indexOf('Her father'),
          end: TEXT.length - 1,
          key: 'k1',
          quoted: 'Her father had drawn the false chart',
        },
      ],
    },
    {
      plan_id: 'pln_s2',
      title: 'The chart room',
      in_plan: true,
      version_id: 'ver_9',
      version_number: 1,
      current: true,
      status: 'not_checked',
      checks: [],
      story_checks: [],
      notes: [],
    },
  ],
  unwritten: [{ plan_id: 'pln_s3', title: 'The father returns' }],
}

describe('AuditDialog', () => {
  it('states what would be audited and its cost, and starts the audit', async () => {
    const api = mockApi({
      'GET /audits/estimate': estimate,
      'GET /audits': { items: [job({ id: 'job_old', created_at: '2026-10-10T09:00:00Z' })] },
      'POST /audits': reply(202, job({ id: 'job_new', status: 'queued', progress: 0 })),
    })
    const user = userEvent.setup()
    const onClose = vi.fn()
    const { container } = renderInProject(<AuditDialog item={beat} onClose={onClose} />)
    const dialog = await screen.findByRole('dialog', { name: 'Audit “She finds the false chart”' })
    expect(await within(dialog).findByText('2 written scenes')).toBeInTheDocument()
    expect(within(dialog).getByText('1 scene not written yet, so left out')).toBeInTheDocument()
    expect(within(dialog).getByText(/About 13,000 tokens in all/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: /Audit of/ })).toHaveAttribute('href', '/projects/prj_1/audits/job_old')
    await expectAccessible(container)

    const choices = within(dialog).getByRole('group', { name: 'What the assistant checks in each scene' })
    await user.click(within(choices).getByRole('checkbox', { name: /Your voice/ }))
    expect(within(dialog).getByText(/About 10,600 tokens in all/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Start the audit' }))
    await waitFor(() => expect(api.find('POST', '/audits')).toHaveLength(1))
    const sent = api.find('POST', '/audits')[0]
    expect(sent.body).toEqual({ plan_id: 'pln_b1', passes: ['viewpoint', 'continuity', 'plan', 'clarity'] })
    expect(sent.headers?.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() =>
      expect(screen.getAllByTestId('location').some((node) => node.textContent === '/projects/prj_1/audits/job_new')).toBe(true),
    )
  })

  it('runs only the free checks when nothing is chosen, and says so', async () => {
    mockApi({ 'GET /audits/estimate': estimate, 'GET /audits': { items: [] } })
    const user = userEvent.setup()
    renderInProject(<AuditDialog item={beat} onClose={vi.fn()} />)
    const choices = await screen.findByRole('group', { name: 'What the assistant checks in each scene' })
    for (const box of within(choices).getAllByRole('checkbox')) await user.click(box)
    expect(screen.getByText('Only the checks without the assistant will run. Nothing is spent.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start the audit' })).toBeEnabled()
  })

  it('cannot start when nothing in that part is written', async () => {
    mockApi({
      'GET /audits/estimate': { ...estimate, written: 0, scenes: [estimate.scenes[2]], total_tokens: 0 },
      'GET /audits': { items: [] },
    })
    renderInProject(<AuditDialog item={beat} onClose={vi.fn()} />)
    expect(
      await screen.findByText('Nothing in this part of the story is written yet, so there is nothing to audit.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start the audit' })).toBeDisabled()
  })

  it('points the start button at no description until one is on the page', async () => {
    mockApi({ 'GET /audits/estimate': estimate, 'GET /audits': { items: [] } })
    renderInProject(<AuditDialog item={beat} onClose={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Start the audit' })
    expect(button).not.toHaveAttribute('aria-describedby')
    await screen.findByText(/About 13,000 tokens in all/)
    expect(button).toHaveAttribute('aria-describedby', 'audit-cost')
    expect(document.getElementById('audit-cost')).not.toBeNull()
  })
})

describe('AuditPage', () => {
  const at = { path: 'audits/:jobId', at: 'audits/job_a' }

  it('reads scene by scene with each passage linked to the studio', async () => {
    mockApi({ 'GET /audits/job_a': { ...report, job: job({ status: 'cancelled' }) } })
    const { container } = renderInProject(<AuditPage />, at)
    expect(await screen.findByRole('heading', { name: 'Audit of “She finds the false chart”' })).toBeInTheDocument()
    expect(screen.getByText('Assistant checks: Viewpoint and secrets, Clarity')).toBeInTheDocument()
    expect(screen.getByText(/The audit was stopped after 1 of 2 scenes/)).toBeInTheDocument()

    const first = screen.getByRole('listitem', { name: 'The lamp room at night' })
    expect(within(first).getByText('Checked')).toBeInTheDocument()
    expect(within(first).getByText(/The scene has changed since/)).toBeInTheDocument()
    const viewpoint = within(first).getByRole('region', { name: 'Viewpoint and secrets' })
    expect(within(viewpoint).getByText('Ines cannot know her father drew it yet.')).toBeInTheDocument()
    expect(within(viewpoint).getByText('Her father had drawn the false chart')).toBeInTheDocument()
    expect(within(viewpoint).getByRole('link', { name: 'Show in the studio' })).toHaveAttribute(
      'href',
      `/projects/prj_1/plan/pln_s1/write?at=${TEXT.indexOf('Her father')}-${TEXT.length - 1}&version=ver_3`,
    )
    expect(within(first).getByRole('region', { name: 'Checks without the assistant' })).toHaveTextContent('stock phrasing')
    expect(within(first).getByRole('region', { name: 'In the plan' })).toHaveTextContent('before it is set up')

    const second = screen.getByRole('listitem', { name: 'The chart room' })
    expect(within(second).getByText('Not checked: the audit stopped first')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'The father returns' })).toHaveAttribute('href', '/projects/prj_1/plan/pln_s3/write')
    await expectAccessible(container)
  })

  it('follows an audit under way and can stop it', async () => {
    const api = mockApi({
      'GET /audits/job_a': {
        ...report,
        job: job({ status: 'running', stage: 'Scene 1 of 2: Checking clarity', progress: 30 }),
        scenes: report.scenes.map((scene) => ({ ...scene, status: 'waiting' })),
      },
      'GET /jobs/job_a': job({ status: 'running', stage: 'Scene 1 of 2: Checking clarity', progress: 30 }),
      'POST /jobs/job_a/cancel': job({ status: 'running', cancel_requested: true }),
    })
    const user = userEvent.setup()
    renderInProject(<AuditPage />, at)
    expect(await screen.findByText('Auditing this part of the story.')).toBeInTheDocument()
    expect(screen.getByText('Scene 1 of 2: Checking clarity')).toBeInTheDocument()
    expect(screen.getAllByText('Waiting or being checked')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(api.find('POST', '/jobs/job_a/cancel')).toHaveLength(1))
  })

  it('follows only the job while the audit runs, and reads the report again when it ends', async () => {
    const running = {
      ...report,
      job: job({ status: 'running', stage: 'Scene 2 of 2: Checking clarity', progress: 80 }),
      scenes: report.scenes.map((scene) => ({ ...scene, status: 'waiting' })),
    }
    let reads = 0
    const api = mockApi({
      'GET /audits/job_a': () => (++reads === 1 ? running : { ...report, job: job() }),
      'GET /jobs/job_a': job(),
    })
    renderInProject(<AuditPage />, at)
    expect(await screen.findByText(/1 note from the assistant/)).toBeInTheDocument()
    expect(api.find('GET', '/jobs/job_a')).toHaveLength(1)
    expect(api.find('GET', '/audits/job_a')).toHaveLength(2)
    expect(screen.queryByText('Auditing this part of the story.')).not.toBeInTheDocument()
  })

  it('does not read the whole report again while the job is still running', async () => {
    const api = mockApi({
      'GET /audits/job_a': {
        ...report,
        job: job({ status: 'running', stage: 'Scene 1 of 2: reading', progress: 10 }),
        scenes: report.scenes.map((scene) => ({ ...scene, status: 'waiting' })),
      },
      'GET /jobs/job_a': job({ status: 'running', stage: 'Scene 2 of 2: Checking clarity', progress: 70 }),
    })
    renderInProject(<AuditPage />, at)
    // The stage comes from the job as followed, not the report as first read.
    expect(await screen.findByText('Scene 2 of 2: Checking clarity')).toBeInTheDocument()
    expect(api.find('GET', '/audits/job_a')).toHaveLength(1)
  })

  it('does not offer a reader the stop button', async () => {
    mockApi({
      'GET /audits/job_a': {
        ...report,
        job: job({ status: 'running', stage: 'Scene 1 of 2: Checking clarity', progress: 30 }),
        scenes: report.scenes.map((scene) => ({ ...scene, status: 'waiting' })),
      },
      'GET /jobs/job_a': job({ status: 'running', stage: 'Scene 1 of 2: Checking clarity', progress: 30 }),
    })
    renderInProject(<AuditPage />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByText('Auditing this part of the story.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
  })

  it('says which checks finished in a scene the audit stopped part of the way through', async () => {
    mockApi({
      'GET /audits/job_a': {
        ...report,
        job: job({ status: 'cancelled' }),
        scenes: [report.scenes[0], { ...report.scenes[1], status: 'partial', passes_done: ['viewpoint'] }],
      },
    })
    renderInProject(<AuditPage />, at)
    const second = await screen.findByRole('listitem', { name: 'The chart room' })
    expect(within(second).getByText('Partly checked: Viewpoint and secrets')).toBeInTheDocument()
    expect(within(second).queryByText(/Not checked/)).not.toBeInTheDocument()
  })

  it('says plainly when the audit cannot be opened', async () => {
    mockApi({ 'GET /audits/job_a': problem(404, 'not_found', 'That audit was not found.') })
    renderInProject(<AuditPage />, at)
    expect(await screen.findByText(/That audit was not found/)).toBeInTheDocument()
  })
})
