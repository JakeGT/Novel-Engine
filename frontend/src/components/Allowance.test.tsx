import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, renderInProject } from '../test/harness'
import { AllowanceNote } from './Allowance'

afterEach(() => vi.unstubAllGlobals())

const usage = {
  period_hours: 24,
  limit_tokens: 200000,
  used_tokens: 20000,
  requests: 4,
  workspace_limit_tokens: 200000,
  site_limit_tokens: 2000000,
}

describe('AllowanceNote', () => {
  it('says about how much a request will read and what is left, before it is sent', async () => {
    mockApi({ 'GET /ai/usage': usage, 'GET /assist/estimate': { story_tokens: 3200, entry_tokens: 1100 } })
    renderInProject(<AllowanceNote reads="story" />)

    expect(
      await screen.findByText(
        'This will read about 3,200 tokens (your whole story bible), plus the reply. 180,000 tokens of your allowance are left for the last day.',
      ),
    ).toBeInTheDocument()
  })

  it('warns once most of the allowance has gone, and says so plainly when none is left', async () => {
    mockApi({ 'GET /ai/usage': { ...usage, used_tokens: 170000 }, 'GET /assist/estimate': { story_tokens: 3200, entry_tokens: 1100 } })
    const first = renderInProject(<AllowanceNote reads="entry" />)
    expect(await screen.findByText(/You have used 85% of your allowance for the last day: 30,000 tokens are left\./)).toBeInTheDocument()
    first.unmount()

    mockApi({ 'GET /ai/usage': { ...usage, used_tokens: 200000 } })
    renderInProject(<AllowanceNote />)
    expect(await screen.findByText(/Your assistant allowance is used up for now/)).toBeInTheDocument()
  })
})
