import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CheckRun } from '../api/client'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { AuditRecord, CheckHistory } from './CheckHistory'

afterEach(() => vi.unstubAllGlobals())

function run(overrides: Partial<CheckRun>): CheckRun {
  return {
    id: 'run_1',
    kind: 'checks',
    scope: 'scene',
    plan_id: 'pln_1',
    title: 'The Docks',
    story_revision: 3,
    passes: ['model_free'],
    job_id: null,
    status: 'done',
    created_at: '2026-10-18T09:00:00Z',
    stale: false,
    scenes: [
      {
        plan_id: 'pln_1',
        title: 'The Docks',
        in_plan: true,
        pictures: [],
        source_id: 'txt_1',
        version_id: 'ver_1',
        draft_id: null,
        stale: false,
        reasons: [],
        open: 1,
        accepted: 0,
        dismissed: 0,
        superseded: 0,
        fixed: 0,
        checks: [
          { name: 'stock', words: 'Stock phrasing', reused: false },
          { name: 'continuity', words: 'Continuity in story time', reused: true },
        ],
      },
    ],
    findings: [
      {
        plan_id: 'pln_1',
        source: 'check',
        code: 'stock_phrase',
        severity: 'note',
        message: '“palpable” is stock phrasing that readers often notice.',
        quoted: 'palpable',
        status: 'open',
        fixed_by: null,
        dismissal: null,
        check_pass: null,
        stale: false,
        came_back: false,
      },
    ],
    profile: { checking: 'on_request', checks: 'scene-checks/1' },
    coverage: {},
    ...overrides,
  }
}

const older = run({
  id: 'run_0',
  kind: 'assistant',
  passes: ['model_free', 'plan'],
  job_id: 'job_1',
  status: 'succeeded',
  stale: true,
  scenes: [
    {
      plan_id: 'pln_1',
      title: 'The Docks',
      in_plan: true,
      pictures: [],
      source_id: 'txt_1',
      version_id: 'ver_0',
      draft_id: null,
      stale: true,
      reasons: ['The words have changed since.'],
      open: 0,
      accepted: 1,
      dismissed: 0,
      superseded: 1,
      fixed: 1,
      checks: [],
    },
  ],
  findings: [
    {
      plan_id: 'pln_1',
      source: 'note',
      code: 'plan',
      severity: 'note',
      message: 'The plan says Mara comes.',
      quoted: 'Mara did not come',
      status: 'fixed',
      fixed_by: 'words',
      dismissal: null,
      check_pass: 'plan',
      stale: false,
      came_back: false,
    },
    {
      plan_id: 'pln_1',
      source: 'check',
      code: 'stock_phrase',
      severity: 'note',
      message: '“palpable” is stock phrasing that readers often notice.',
      quoted: 'palpable',
      status: 'accepted',
      fixed_by: null,
      dismissal: { id: 'dis_1', reason: 'intended', note: '', expires_at: null, created_at: '2026-10-18T08:00:00Z' },
      check_pass: null,
      stale: false,
      came_back: false,
    },
    {
      plan_id: 'pln_1',
      source: 'check',
      code: 'secret',
      severity: 'warning',
      message: 'This may give away a secret Tobin does not know.',
      quoted: 'the guild',
      status: 'superseded',
      fixed_by: null,
      dismissal: null,
      check_pass: null,
      stale: false,
      came_back: false,
    },
  ],
  coverage: {
    continuity: {
      sent: 'groups',
      groups: 3,
      complete: true,
      lines: ['6 passages from 1 scene, read in 3 groups; about 2400 tokens in all.'],
      passages: [
        { start: 0, end: 10, checks: ['Where and when people and things are'] },
        { start: 12, end: 30, checks: ['Where and when people and things are', 'Condition, injury and life'] },
      ],
    },
  },
})

const CALIBRATION = {
  items: [
    {
      code: 'stock_phrase',
      words: 'Stock phrasing',
      source: 'check',
      found: 6,
      dismissed: 4,
      accepted: 1,
      rate: 0.67,
      note: 'Found something 6 times. You said it was not a problem 4 times (67%).',
    },
    { code: 'new_name', words: 'A new name', source: 'check', found: 0, dismissed: 0, accepted: 0, rate: null, note: 'Nothing found yet.' },
  ],
}

const pictured = run({
  id: 'run_2',
  kind: 'assistant',
  passes: ['model_free', 'pictures'],
  job_id: 'job_2',
  status: 'succeeded',
  stale: true,
  scenes: [
    {
      ...run({}).scenes[0],
      open: 1,
      pictures: [
        {
          asset_id: 'ast_1',
          version_id: 'av_1',
          label: '“Mara on the quay”, version 1',
          choice: 'send',
          changed: 'A newer version of it has been added since.',
        },
        { asset_id: 'ast_2', version_id: 'av_2', label: '“The docks”, version 1', choice: 'describe', changed: null },
      ],
    },
  ],
  findings: [
    {
      plan_id: 'pln_1',
      source: 'note',
      code: 'pictures',
      severity: 'note',
      message: 'Her picture shows a blue coat. Compared with the picture “Mara on the quay”, version 1.',
      quoted: 'her red coat',
      status: 'open',
      fixed_by: null,
      dismissal: null,
      check_pass: 'pictures',
      stale: true,
      came_back: false,
      picture: {
        asset_id: 'ast_1',
        version_id: 'av_1',
        label: '“Mara on the quay”, version 1',
        choice: 'send',
        changed: 'A newer version of it has been added since.',
      },
    },
  ],
})

describe('CheckHistory pictures', () => {
  it('says which pictures a check compared the scene with and which have changed since', async () => {
    mockApi({
      'GET /plan/pln_1/checks/runs': { items: [pictured] },
      'GET /checks/calibration': { items: [] },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<CheckHistory planId="pln_1" versions={new Map()} refresh="a" />)
    const section = await screen.findByRole('region', { name: 'Check history' })
    expect(await within(section).findByText('Compared with 2 pictures:')).toBeInTheDocument()
    expect(within(section).getByText(/“Mara on the quay”, version 1 \(the picture itself\)/)).toBeInTheDocument()
    expect(within(section).getByText(/— A newer version of it has been added since\./)).toBeInTheDocument()
    expect(within(section).getByText(/“The docks”, version 1 \(its description\)/)).toBeInTheDocument()
    // The scene's words have not changed, but a picture has: the run is out of date.
    expect(within(section).getByText('Out of date')).toBeInTheDocument()
    expect(within(section).getByText('A picture it read has changed.')).toBeInTheDocument()
    await user.click(within(section).getByText('Show the finding'))
    expect(
      within(section).getByText(
        'The picture it was compared with has changed since: A newer version of it has been added since.',
      ),
    ).toBeInTheDocument()
    await expectAccessible(container)
  })
})

describe('CheckHistory', () => {
  it('lists each check with the version it read, whether it is out of date, and what became of it', async () => {
    const api = mockApi({
      'GET /plan/pln_1/checks/runs': { items: [run({}), older] },
      'POST /plan/pln_1/checks/runs': run({}),
      'GET /checks/calibration': CALIBRATION,
    })
    const user = userEvent.setup()
    const versions = new Map([
      ['ver_1', 2],
      ['ver_0', 1],
    ])
    const { container } = renderInProject(<CheckHistory planId="pln_1" versions={versions} refresh="a" />)
    const section = await screen.findByRole('region', { name: 'Check history' })
    await within(section).findByText('Checks without the assistant')
    const items = await within(section).findAllByRole('listitem')
    const [newest, oldest] = items.filter((item) => item.classList.contains('check-run'))
    expect(within(newest).getByText('Checks without the assistant')).toBeInTheDocument()
    expect(within(newest).getByText('Version 2')).toBeInTheDocument()
    expect(within(newest).getByText('Up to date')).toBeInTheDocument()
    expect(within(newest).getByText('1 open')).toBeInTheDocument()
    // Which checks it worked out, and which it took as they were (CON-007).
    expect(
      within(newest).getByText('1 of 2 checks were worked out again; continuity in story time read nothing that had changed.'),
    ).toBeInTheDocument()
    expect(within(oldest).getByText('1 kept on purpose · 1 found again later · 1 fixed')).toBeInTheDocument()
    // How a long scene was sent in groups, with what each passage got (CON-013).
    expect(
      within(oldest).getByText(/Continuity with the story bible: sent in 3 groups\. Every passage got every check it needs\./),
    ).toBeInTheDocument()
    expect(within(oldest).getByText('Check with the assistant: What the plan asks for')).toBeInTheDocument()
    expect(within(oldest).getByText('Out of date')).toBeInTheDocument()
    expect(within(oldest).getByText(/The words have changed since/)).toBeInTheDocument()

    await user.click(within(oldest).getByText('Show the 3 findings'))
    expect(within(oldest).getByText('Fixed: the words changed')).toBeInTheDocument()
    expect(within(oldest).getByText('Kept on purpose')).toBeInTheDocument()
    expect(within(oldest).getByText('Found again by a later check')).toBeInTheDocument()

    // How often each check is dismissed, only for checks that found something (CON-016).
    const calibration = await within(section).findByRole('region', { name: 'How often you dismiss each check' })
    const row = within(calibration).getByText('Stock phrasing').closest('li') as HTMLElement
    expect(within(row).getByText('Found 6 · not a problem 4 · kept on purpose 1')).toBeInTheDocument()
    expect(within(row).getByText(/not a problem 4 times \(67%\)/)).toBeInTheDocument()
    expect(within(calibration).queryByText('A new name')).not.toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(section).getByRole('button', { name: 'Keep a record of the checks now' }))
    await waitFor(() => expect(api.find('POST', '/plan/pln_1/checks/runs')).toHaveLength(1))
  })

  it('says plainly when nothing has been kept yet', async () => {
    mockApi({ 'GET /plan/pln_1/checks/runs': { items: [] }, 'GET /checks/calibration': { items: [] } })
    renderInProject(<CheckHistory planId="pln_1" versions={new Map()} refresh="" />)
    expect(await screen.findByText(/None yet\. Saving the scene keeps a record/)).toBeInTheDocument()
  })
})

describe('AuditRecord', () => {
  it('shows, scene by scene, what has changed since the audit read it', async () => {
    mockApi({
      'GET /checks/runs/by-job/job_7': run({
        kind: 'audit',
        scope: 'part',
        title: 'The wait',
        job_id: 'job_7',
        stale: true,
        scenes: [older.scenes[0]],
      }),
    })
    const { container } = renderInProject(<AuditRecord jobId="job_7" refresh="succeeded" />)
    const section = await screen.findByRole('region', { name: 'Since this audit' })
    expect(within(section).getByText(/Some scenes have changed since the audit read them/)).toBeInTheDocument()
    expect(within(section).getByRole('cell', { name: 'The words have changed since.' })).toBeInTheDocument()
    expect(
      within(section).getByRole('cell', { name: '1 kept on purpose · 1 found again later · 1 fixed' }),
    ).toBeInTheDocument()
    await expectAccessible(container)
  })
})
