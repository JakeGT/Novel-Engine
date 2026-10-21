import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WhatIfAudit } from '../api/client'
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

describe('WhatIfAuditPage', () => {
  it('lists what differs, with the checks found only in the what-if', async () => {
    mockApi({ 'GET /what-ifs/br_2/audit': audit })
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
})
