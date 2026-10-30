import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  expectAccessible,
  mockApi,
  problem,
  renderInProject,
  reply,
  type RecordedCall,
} from '../test/harness'
import { AssistantPage } from './AssistantPage'

afterEach(() => vi.unstubAllGlobals())

const providers = [
  { id: 'openrouter', label: 'OpenRouter', key_url: 'https://openrouter.ai/keys', default_model: 'anthropic/claude-sonnet-5.5', note: 'One key for models from many companies.' },
  { id: 'anthropic', label: 'Anthropic (Claude)', key_url: 'https://console.anthropic.com/settings/keys', default_model: 'claude-sonnet-5-5', note: '' },
  { id: 'openai', label: 'OpenAI', key_url: 'https://platform.openai.com/api-keys', default_model: null, note: '' },
]
function credential(provider: string, overrides = {}) {
  return {
    id: `cred_${provider}`,
    workspace_id: 'ws_1',
    provider,
    label: provider,
    key_fingerprint: 'fp',
    key_last4: 'ab12',
    key_version: 1,
    status: 'active',
    created_at: new Date().toISOString(),
    rotated_at: null,
    last_used_at: null,
    revoked_at: null,
    verified: null,
    ...overrides,
  }
}

describe('AssistantPage', () => {
  it('connects a key for a chosen provider and says where to get one', async () => {
    const api = mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [],
      'GET /ai/settings': { provider: null, model: null, chosen: false },
      'POST /credentials': reply(201, credential('anthropic')),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<AssistantPage />)

    expect(await screen.findByText(/No key is connected, so the assistant is switched off/)).toBeInTheDocument()
    // With no key there is no model to choose yet.
    expect(screen.queryByRole('region', { name: 'Model in use' })).not.toBeInTheDocument()
    const form = screen.getByRole('region', { name: 'Connect a key' })
    expect(within(form).getByRole('link', { name: 'openrouter.ai/keys' })).toHaveAttribute('href', 'https://openrouter.ai/keys')
    await expectAccessible(container)

    await user.selectOptions(within(form).getByLabelText('Provider'), 'Anthropic (Claude)')
    expect(within(form).getByRole('link', { name: 'console.anthropic.com/settings/keys' })).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Connect key' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('looks too short')
    expect(api.find('POST', '/credentials')).toHaveLength(0)

    await user.type(within(form).getByLabelText('Key'), 'sk-ant-secret-key-1')
    await user.click(within(form).getByRole('button', { name: 'Connect key' }))
    await waitFor(() => expect(api.find('POST', '/credentials')).toHaveLength(1))
    // The name is optional: it falls back to the provider's own.
    expect(api.find('POST', '/credentials')[0].body).toEqual({
      provider: 'anthropic',
      label: 'Anthropic (Claude)',
      key: 'sk-ant-secret-key-1',
    })
  })

  it('says when the provider could not be reached to check the key', async () => {
    const api = mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [],
      'GET /ai/settings': { provider: null, model: null, chosen: false },
      'POST /credentials': reply(201, credential('openrouter', { verified: false })),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<AssistantPage />)

    const form = await screen.findByRole('region', { name: 'Connect a key' })
    await user.type(within(form).getByLabelText('Key'), 'sk-or-secret-key-1')
    await user.click(within(form).getByRole('button', { name: 'Connect key' }))

    await waitFor(() => expect(api.find('POST', '/credentials')).toHaveLength(1))
    expect(await within(form).findByRole('status')).toHaveTextContent(
      'Saved. OpenRouter could not be reached to check the key, so it will be tried the first time the assistant is used.',
    )
    await expectAccessible(container)
  })

  it('keeps a refused key in the field and shows the provider message', async () => {
    mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [],
      'GET /ai/settings': { provider: null, model: null, chosen: false },
      'POST /credentials': problem(
        400,
        'validation_error',
        'OpenRouter did not accept that key. Check it was copied in full and has not been revoked.',
      ),
    })
    const user = userEvent.setup()
    renderInProject(<AssistantPage />)

    const form = await screen.findByRole('region', { name: 'Connect a key' })
    await user.type(within(form).getByLabelText('Key'), 'sk-or-bad-key-1234')
    await user.click(within(form).getByRole('button', { name: 'Connect key' }))

    const alert = await within(form).findByRole('alert')
    expect(alert).toHaveTextContent('did not accept that key')
    expect(within(form).getByLabelText('Key')).toHaveValue('sk-or-bad-key-1234')
  })

  it('shows the model in use and lets an owner pick another from the provider’s list', async () => {
    const api = mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [credential('openrouter'), credential('openai')],
      'GET /ai/settings': { provider: 'openrouter', model: 'anthropic/claude-sonnet-5.5', chosen: false },
      'GET /ai/models': (call: RecordedCall) =>
        call.search.get('provider') === 'openai'
          ? { models: ['gpt-large', 'gpt-small'], listed: true }
          : { models: ['anthropic/claude-sonnet-5.5'], listed: true },
      'PUT /ai/settings': { provider: 'openai', model: 'gpt-large', chosen: true },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<AssistantPage />)

    const section = await screen.findByRole('region', { name: 'Model in use' })
    expect(section).toHaveTextContent('The assistant is using anthropic/claude-sonnet-5.5 from OpenRouter.')
    expect(section).toHaveTextContent('This is the suggested model; you can pick another.')
    const keys = screen.getByRole('region', { name: 'Connected keys' })
    const rows = within(keys).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('In use')
    expect(rows[1]).toHaveTextContent('Connected, not in use')
    await expectAccessible(container)

    // Only connected providers can be chosen; switching starts from that provider's own models.
    const provider = within(section).getByLabelText('Provider')
    expect(within(provider).getAllByRole('option').map((option) => option.textContent)).toEqual(['OpenRouter', 'OpenAI'])
    await user.selectOptions(provider, 'OpenAI')
    expect(within(section).getByLabelText('Model')).toHaveValue('')
    expect(await within(section).findByText(/Pick one of the 2 models your key can use/)).toBeInTheDocument()

    await user.click(within(section).getByRole('button', { name: 'Use this model' }))
    expect(await within(section).findByRole('alert')).toHaveTextContent('Choose a model')
    await user.type(within(section).getByLabelText('Model'), 'gpt-large')
    await user.click(within(section).getByRole('button', { name: 'Use this model' }))

    await waitFor(() => expect(api.find('PUT', '/ai/settings')).toHaveLength(1))
    expect(api.find('PUT', '/ai/settings')[0].body).toEqual({ provider: 'openai', model: 'gpt-large' })
    expect(await screen.findByText('The assistant now uses gpt-large from OpenAI.')).toBeInTheDocument()
  })

  it('says a model must be chosen when the provider has no suggestion, and still lets one be typed', async () => {
    mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [credential('openai')],
      'GET /ai/settings': { provider: 'openai', model: null, chosen: false },
      'GET /ai/models': { models: [], listed: false },
    })
    renderInProject(<AssistantPage />)

    const section = await screen.findByRole('region', { name: 'Model in use' })
    expect(within(section).getByRole('note')).toHaveTextContent('Choose a model to switch the assistant on.')
    expect(await within(section).findByText(/did not give a list\. Type the model name/)).toBeInTheDocument()
    // A second key for the same provider is not offered; the existing one is replaced by removing it.
    const form = screen.getByRole('region', { name: 'Connect a key' })
    await userEvent.setup().selectOptions(within(form).getByLabelText('Provider'), 'OpenAI (connected)')
    expect(within(form).getByRole('button', { name: 'Connect key' })).toBeDisabled()
    expect(form).toHaveTextContent('already connected; remove it first to replace it')
  })

  it('tells a member who is not an owner which model is in use, and offers no controls', async () => {
    mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': problem(403, 'forbidden', 'Not allowed.'),
      'GET /ai/settings': { provider: 'anthropic', model: 'claude-sonnet-5-5', chosen: true },
    })
    renderInProject(<AssistantPage />)

    expect(await screen.findByText(/Only a workspace owner can connect a key or change the model/)).toHaveTextContent(
      'The assistant is using claude-sonnet-5-5 from Anthropic (Claude).',
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
  it('shows what has been used of the daily allowance and lets an owner set it', async () => {
    let allowance = {
      period_hours: 24,
      limit_tokens: 2000000,
      used_tokens: 12400,
      requests: 3,
      workspace_limit_tokens: null as number | null,
      site_limit_tokens: 2000000,
    }
    const api = mockApi({
      'GET /ai/providers': providers,
      'GET /credentials': [credential('openrouter')],
      'GET /ai/settings': { provider: 'openrouter', model: 'm', chosen: true },
      'GET /ai/models': { models: ['m'], listed: true },
      'GET /ai/usage': () => allowance,
      'PUT /ai/usage': () => (allowance = { ...allowance, limit_tokens: 200000, workspace_limit_tokens: 200000 }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<AssistantPage />)

    const card = await screen.findByRole('region', { name: 'Daily allowance' })
    expect(card).toHaveTextContent('You have used 12,400 of 2,000,000 tokens in the last day, over 3 requests.')
    await expectAccessible(container)

    const box = within(card).getByLabelText('Allowance for each person, per day (tokens)')
    await user.type(box, 'lots')
    await user.click(within(card).getByRole('button', { name: 'Save allowance' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('Enter a whole number')
    expect(api.find('PUT', '/ai/usage')).toHaveLength(0)

    await user.clear(box)
    await user.type(box, '200,000')
    await user.click(within(card).getByRole('button', { name: 'Save allowance' }))
    await waitFor(() => expect(card).toHaveTextContent('of 200,000 tokens'))
    expect(api.find('PUT', '/ai/usage')[0].body).toEqual({ daily_tokens: 200000 })
  })
})
