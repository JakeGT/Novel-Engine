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
        source_id: 'txt_1',
        version_id: 'ver_1',
        draft_id: null,
        stale: false,
        reasons: [],
        open: 1,
        dismissed: 0,
        fixed: 0,
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
      },
    ],
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
      source_id: 'txt_1',
      version_id: 'ver_0',
      draft_id: null,
      stale: true,
      reasons: ['The words have changed since.'],
      open: 0,
      dismissed: 1,
      fixed: 1,
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
    },
    {
      plan_id: 'pln_1',
      source: 'check',
      code: 'stock_phrase',
      severity: 'note',
      message: '“palpable” is stock phrasing that readers often notice.',
      quoted: 'palpable',
      status: 'dismissed',
      fixed_by: null,
      dismissal: { id: 'dis_1', reason: 'intended', note: '', expires_at: null, created_at: '2026-10-18T08:00:00Z' },
      check_pass: null,
      stale: false,
    },
  ],
})

describe('CheckHistory', () => {
  it('lists each check with the version it read, whether it is out of date, and what became of it', async () => {
    const api = mockApi({
      'GET /plan/pln_1/checks/runs': { items: [run({}), older] },
      'POST /plan/pln_1/checks/runs': run({}),
    })
    const user = userEvent.setup()
    const versions = new Map([
      ['ver_1', 2],
      ['ver_0', 1],
    ])
    const { container } = renderInProject(<CheckHistory planId="pln_1" versions={versions} refresh="a" />)
    const section = await screen.findByRole('region', { name: 'Check history' })
    const items = await within(section).findAllByRole('listitem')
    const [newest, oldest] = items.filter((item) => item.classList.contains('check-run'))
    expect(within(newest).getByText('Checks without the assistant')).toBeInTheDocument()
    expect(within(newest).getByText('Version 2')).toBeInTheDocument()
    expect(within(newest).getByText('Up to date')).toBeInTheDocument()
    expect(within(newest).getByText('1 open · 0 dismissed · 0 fixed')).toBeInTheDocument()
    expect(within(oldest).getByText('Check with the assistant: What the plan asks for')).toBeInTheDocument()
    expect(within(oldest).getByText('Out of date')).toBeInTheDocument()
    expect(within(oldest).getByText(/The words have changed since/)).toBeInTheDocument()

    await user.click(within(oldest).getByText('Show the 2 findings'))
    expect(within(oldest).getByText('Fixed: the words changed')).toBeInTheDocument()
    expect(within(oldest).getByText('Dismissed: intended')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(section).getByRole('button', { name: 'Keep a record of the checks now' }))
    await waitFor(() => expect(api.find('POST', '/plan/pln_1/checks/runs')).toHaveLength(1))
  })

  it('says plainly when nothing has been kept yet', async () => {
    mockApi({ 'GET /plan/pln_1/checks/runs': { items: [] } })
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
    expect(within(section).getByRole('cell', { name: '0 open · 1 dismissed · 1 fixed' })).toBeInTheDocument()
    await expectAccessible(container)
  })
})
