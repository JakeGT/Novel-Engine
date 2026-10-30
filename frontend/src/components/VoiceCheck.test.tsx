import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { StudioJob, VoiceEscalation } from '../api/client'
import { expectAccessible, mockApi, renderInProject, reply } from '../test/harness'
import { AskAboutVoice } from './VoiceCheck'

afterEach(() => vi.unstubAllGlobals())

const TEXT = 'I walk down to the harbor before the light comes up.\n\nShe waited.'
const offered: VoiceEscalation = {
  offered: true,
  why_not: null,
  notes: ['This scene is told with “I” in the narration.'],
  passages: [{ start: 0, end: 52, why: 'This scene is told with “I” in the narration.' }],
  words: 11,
  total_tokens: 1300,
  version_id: 'ver_1',
}

function job(overrides: Partial<StudioJob>): StudioJob {
  return {
    id: 'job_v',
    kind: 'voice_check',
    status: 'succeeded',
    stage: '',
    progress: 100,
    cancel_requested: false,
    prompt_tokens: 100,
    completion_tokens: 20,
    result: { findings: 1, dropped: 0, passages: 1 },
    ...overrides,
  } as StudioJob
}

const props = {
  planId: 'pln_1',
  versionId: 'ver_1',
  notesKey: 'k1',
  text: TEXT,
  point: (start?: number | null, end?: number | null) =>
    start == null || end == null ? null : { start, end },
}

describe('AskAboutVoice', () => {
  it('shows the passages and the cost first, then says what the assistant found', async () => {
    const api = mockApi({
      'GET /plan/pln_1/voice/escalation': offered,
      'POST /plan/pln_1/voice/escalation': reply(202, job({ status: 'queued', result: {} })),
      'GET /jobs/job_v': job({}),
    })
    const onShow = vi.fn()
    const user = userEvent.setup()
    const { container } = renderInProject(<AskAboutVoice {...props} onShow={onShow} />)
    await user.click(await screen.findByRole('button', { name: 'Ask the assistant about the passages that show it' }))
    const offer = screen.getByRole('group', { name: 'Ask the assistant how these passages sound' })
    expect(within(offer).getByText(/About 1,300 tokens/)).toHaveTextContent('reads only 1 passage (11 words)')
    expect(within(offer).getByText(/“I walk down to the harbor before the…”/)).toBeInTheDocument()
    await user.click(within(offer).getByRole('button', { name: 'Show in the text' }))
    expect(onShow).toHaveBeenCalledWith({ start: 0, end: 52 })
    await expectAccessible(container)
    expect(api.find('POST', '/voice/escalation')).toHaveLength(0)
    await user.click(within(offer).getByRole('button', { name: 'Ask the assistant' }))
    expect(await screen.findByText('Done: 1 note from the assistant, with its other notes on this scene.')).toBeInTheDocument()
  })

  it('is not offered when the measures do not point far enough', async () => {
    const api = mockApi({
      'GET /plan/pln_1/voice/escalation': { ...offered, offered: false, why_not: 'Not far enough.', passages: [] },
    })
    renderInProject(<AskAboutVoice {...props} onShow={vi.fn()} />)
    await waitFor(() => expect(api.find('GET', '/voice/escalation')).toHaveLength(1))
    expect(screen.queryByRole('button', { name: /Ask the assistant/ })).not.toBeInTheDocument()
  })
})
