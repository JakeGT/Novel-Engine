import { afterEach, describe, expect, it, vi } from 'vitest'
import { setSpendAsker, type SpendQuestion } from '../lib/spend'
import { mockApi, problem } from '../test/harness'
import { api, isApiError } from './client'

afterEach(() => {
  setSpendAsker(null)
  vi.unstubAllGlobals()
})

const scope = { workspaceId: 'ws_1', projectId: 'prj_1' }

function refusal(tokens: number) {
  return problem(409, 'needs_confirmation', `Checking this scene will use about ${tokens} tokens.`, {
    estimated_tokens: String(tokens),
    threshold_tokens: '50000',
  })
}

describe('Asking before a large request', () => {
  it('asks once more when the estimate grew between asking and sending', async () => {
    const answers = [refusal(64000), refusal(70000), { id: 'job_1', kind: 'scene_review', status: 'queued' }]
    const mock = mockApi({ 'POST /studio/reviews': () => answers.shift() })
    const asked: SpendQuestion[] = []
    setSpendAsker(async (question) => {
      asked.push(question)
      return true
    })

    await expect(api.startReview(scope, 'pln_1', null)).resolves.toMatchObject({ id: 'job_1' })
    expect(asked.map((question) => question.estimatedTokens)).toEqual([64000, 70000])
    const calls = mock.find('POST', '/studio/reviews')
    expect(calls.map((call) => call.headers?.get('X-Confirm-Tokens'))).toEqual([null, '64000', '70000'])
  })

  it('sends back what it was shown when the question is about pictures', async () => {
    const sending = {
      provider: 'openrouter',
      provider_label: 'OpenRouter',
      model: 'openai/gpt-4o',
      destination: 'openrouter.ai',
      where: 'company',
      where_words: 'to OpenRouter’s servers, outside this site',
      words: 100,
      estimated_tokens: 500,
      pictures: [{ asset_id: 'ast_1', version_id: 'ver_1', name: 'Mara' }],
      digest: 'ab12cd34',
    }
    const asking = refusal(500)
    ;(asking.body as Record<string, unknown>).sending = sending
    const answers = [asking, { id: 'job_1', kind: 'scene_review', status: 'queued' }]
    const mock = mockApi({ 'POST /studio/reviews': () => answers.shift() })
    setSpendAsker(async () => true)

    await expect(api.startReview(scope, 'pln_1', null)).resolves.toMatchObject({ id: 'job_1' })
    const calls = mock.find('POST', '/studio/reviews')
    expect(calls.map((call) => call.headers?.get('X-Confirm-Sending'))).toEqual([null, 'ab12cd34'])
    expect(calls.map((call) => call.headers?.get('X-Confirm-Tokens'))).toEqual([null, '500'])
  })

  it('stops after asking twice and shows the refusal', async () => {
    const mock = mockApi({ 'POST /studio/reviews': refusal(64000) })
    setSpendAsker(async () => true)
    const error = await api.startReview(scope, 'pln_1', null).catch((found: unknown) => found)
    expect(isApiError(error, 'needs_confirmation')).toBe(true)
    expect(mock.find('POST', '/studio/reviews')).toHaveLength(3)
  })
})
