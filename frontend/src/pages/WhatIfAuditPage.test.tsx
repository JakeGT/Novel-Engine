import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuditEstimate, WhatIfAudit } from '../api/client'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { WhatIfAuditPage } from './WhatIfAuditPage'

afterEach(() => vi.unstubAllGlobals())

const audit: WhatIfAudit = {
  what_if_id: 'br_2',
  what_if_name: 'Mara stays away',
  same: false,
  bible: [
    { kind: 'fact', change: 'removed', title: 'Mara is a member of Harbor Guild', detail: 'True in the main story only', plan_id: null },
    { kind: 'entry', change: 'added', title: 'Ines', detail: 'Character', plan_id: null },
  ],
  plan: [{ kind: 'plan', change: 'changed', title: 'The quay', detail: 'what happens differs', plan_id: 'pln_q' }],
  scenes: [
    {
      plan_id: 'pln_two',
      title: 'Two',
      main_words: 700,
      what_if_words: 720,
      voice: ['This scene is told with “I” in the narration; your other scenes are told with “he”, “she” or “they”.'],
    },
  ],
  new_findings: ['Mara is in two places at once.'],
  gone_findings: [],
  left_out: [],
}

const changed: AuditEstimate = {
  plan_id: 'what-if-changes',
  title: 'The scenes written differently in this what-if',
  scenes: [{ plan_id: 'pln_two', title: 'Two', written: true, words: 720, total_tokens: 1300 }],
  written: 1,
  max_scenes: 60,
  passes: [
    { check_pass: 'clarity', label: 'Clarity', description: 'What is unclear.', prompt_tokens: 1200, completion_tokens: 100, total_tokens: 1300 },
  ],
  total_tokens: 1300,
} as AuditEstimate

describe('WhatIfAuditPage', () => {
  it('lists what differs, with the checks found only in the what-if', async () => {
    mockApi({ 'GET /what-ifs/br_2/audit': audit, 'GET /audit/changed/estimate': changed })
    const { container } = renderInProject(<WhatIfAuditPage />, { path: 'what-ifs/:branchId/audit', at: 'what-ifs/br_2/audit' })
    expect(await screen.findByRole('heading', { name: 'How “Mara stays away” differs from your story' })).toBeInTheDocument()
    const checks = screen.getByRole('region', { name: 'Checks' })
    expect(within(checks).getByText('Mara is in two places at once.')).toBeInTheDocument()
    const scenes = screen.getByRole('region', { name: 'Scenes written differently' })
    expect(within(scenes).getByRole('link', { name: 'Two' })).toHaveAttribute('href', '/projects/prj_1/plan/pln_two/write')
    expect(within(scenes).getByText(/told with “I”/)).toBeInTheDocument()
    const bible = screen.getByRole('region', { name: 'The story bible' })
    expect(within(bible).getByText('Only in your story')).toBeInTheDocument()
    expect(within(bible).getByText('Mara is a member of Harbor Guild')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('says so when nothing differs', async () => {
    mockApi({ 'GET /what-ifs/br_2/audit': { ...audit, same: true, bible: [], plan: [], scenes: [], new_findings: [] } })
    renderInProject(<WhatIfAuditPage />, { path: 'what-ifs/:branchId/audit', at: 'what-ifs/br_2/audit' })
    expect(await screen.findByText('Nothing differs yet: the what-if still matches your story.')).toBeInTheDocument()
  })

  it('asks the assistant about only the changed scenes, with the cost first', async () => {
    window.localStorage.clear()
    const api = mockApi({
      'GET /what-ifs/br_2/audit': audit,
      'GET /audit/changed/estimate': changed,
      'POST /what-ifs/br_2/audit/changed': { id: 'job_9', status: 'queued' },
      'GET /audits/job_9': {},
    })
    const { container } = renderInProject(<WhatIfAuditPage />, { path: 'what-ifs/:branchId/audit', at: 'what-ifs/br_2/audit' })
    const section = await screen.findByRole('region', { name: 'Ask the assistant about the changed scenes' })
    expect(await within(section).findByText(/About 1,300 tokens in all/)).toBeInTheDocument()
    await expectAccessible(container)
    await userEvent.click(within(section).getByRole('button', { name: 'Check the changed scene' }))
    const [sent] = api.find('POST', '/what-ifs/br_2/audit/changed')
    expect(sent.body).toEqual({ passes: expect.arrayContaining(['clarity']) })
    expect(sent.headers?.get('Idempotency-Key')).toBeTruthy()
    const [where] = await screen.findAllByTestId('location')
    expect(where).toHaveTextContent('/projects/prj_1/audits/job_9')
  })

  it('offers nothing to check when no scene is written differently', async () => {
    mockApi({ 'GET /what-ifs/br_2/audit': { ...audit, scenes: [] } })
    renderInProject(<WhatIfAuditPage />, { path: 'what-ifs/:branchId/audit', at: 'what-ifs/br_2/audit' })
    const section = await screen.findByRole('region', { name: 'Ask the assistant about the changed scenes' })
    expect(within(section).getByText(/nothing for the assistant to check/)).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: /Check/ })).toBeDisabled()
  })
})
