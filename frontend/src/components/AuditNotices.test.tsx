import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuthorNotice } from '../api/client'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { AuditNotices } from './AuditNotices'

afterEach(() => vi.unstubAllGlobals())

const notice: AuthorNotice = {
  id: 'ntc_1',
  kind: 'audit_finished',
  job_id: 'job_7',
  status: 'succeeded',
  sentence:
    'Your scheduled audit of “Act One” finished. It read 5 scenes and found 3 notes from the assistant and 2 findings from the checks without it. It used 12,400 tokens (about $0.02).',
  part: '“Act One”',
  plan_id: 'pln_act',
  what_if: null,
  branch_id: null,
  scenes: 5,
  notes: 3,
  checks: 2,
  total_tokens: 12400,
  cost_usd: 0.02,
  created_at: '2026-10-21T08:00:00Z',
  seen: false,
}

describe('AuditNotices', () => {
  it('tells the author what a scheduled audit found and used, until marked read', async () => {
    let seen = false
    const api = mockApi({
      'GET /notices': () => ({ items: seen ? [] : [notice] }),
      'POST /notices/ntc_1/seen': () => {
        seen = true
        return {}
      },
    })
    const { container } = renderInProject(<AuditNotices />)
    const region = await screen.findByRole('region', { name: 'Audits you scheduled' })
    expect(within(region).getByText(/found 3 notes from the assistant/)).toBeInTheDocument()
    expect(within(region).getByText(/12,400 tokens/)).toBeInTheDocument()
    expect(within(region).getByRole('link', { name: 'Open the audit' })).toHaveAttribute(
      'href',
      '/projects/prj_1/audits/job_7',
    )
    expect(api.find('GET', '/notices')[0].search.get('unseen')).toBe('true')
    await expectAccessible(container)
    await userEvent.click(within(region).getByRole('button', { name: 'Mark as read' }))
    expect(api.find('POST', '/notices/ntc_1/seen')).toHaveLength(1)
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Audits you scheduled' })).toBeNull())
  })

  it('shows one audit’s notice on its page, read or not, in the what-if it ran in', async () => {
    const api = mockApi({ 'GET /notices': { items: [{ ...notice, seen: true, what_if: 'Mara stays' }] } })
    renderInProject(<AuditNotices jobId="job_7" />)
    const region = await screen.findByRole('region', { name: 'Your scheduled audit' })
    expect(within(region).getByText('In the what-if “Mara stays”')).toBeInTheDocument()
    expect(within(region).getByText('Read')).toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: 'Mark as read' })).toBeNull()
    expect(within(region).queryByRole('link', { name: 'Open the audit' })).toBeNull()
    expect(api.find('GET', '/notices')[0].search.get('job_id')).toBe('job_7')
  })

  it('shows nothing when there is nothing to tell', async () => {
    const api = mockApi({ 'GET /notices': { items: [] } })
    renderInProject(<AuditNotices />)
    await vi.waitFor(() => expect(api.find('GET', '/notices')).toHaveLength(1))
    expect(screen.queryByRole('region')).toBeNull()
  })
})
