import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, problem } from '../test/harness'
import { SitePage } from './SitePage'

afterEach(() => vi.unstubAllGlobals())

const ADMIN_SESSION = {
  user: { id: 'usr_1', email: 'jake@example.com', display_name: 'Jake Talling' },
  auth_mode: 'hosted',
  platform_admin: true,
}

const OPENAI = {
  id: 'openai',
  label: 'OpenAI',
  group: 'Model makers',
  base_url: null,
  needs_url: false,
  key_url: 'https://platform.openai.com/api-keys',
  key_hint: 'Starts with sk-',
  key_optional_when_local: false,
  docs: 'https://platform.openai.com/docs/embeddings',
  models: [
    { id: 'text-embedding-3-small', dimension: 1536, adjustable: true, default_dimension: 1536 },
    { id: 'text-embedding-3-large', dimension: 3072, adjustable: true, default_dimension: 3072 },
    { id: 'text-embedding-ada-002', dimension: 1536, adjustable: false, default_dimension: 1536 },
  ],
  dimension_choices: null,
}

const VOYAGE = {
  id: 'voyage',
  label: 'Voyage AI',
  group: 'Model makers',
  base_url: null,
  needs_url: false,
  key_url: 'https://dash.voyageai.com/api-keys',
  key_hint: 'Starts with pa-',
  key_optional_when_local: false,
  docs: 'https://docs.voyageai.com',
  models: [{ id: 'voyage-3-large', dimension: 1024, adjustable: true, default_dimension: 1024 }],
  dimension_choices: [256, 512, 1024, 2048],
}

const OLLAMA = {
  id: 'ollama',
  label: 'Ollama',
  group: 'Your own',
  base_url: 'http://localhost:11434/v1',
  needs_url: true,
  key_url: null,
  key_hint: null,
  key_optional_when_local: true,
  docs: 'https://ollama.com',
  models: [{ id: 'nomic-embed-text', dimension: 768, adjustable: true, default_dimension: 768 }],
  dimension_choices: null,
}

const PROVIDERS = [OPENAI, VOYAGE, OLLAMA]

function pageSetup(overrides: Record<string, unknown> = {}) {
  return {
    source: 'page',
    editable: true,
    kek_configured: true,
    setup: {
      provider: 'openai',
      provider_label: 'OpenAI',
      model: 'text-embedding-3-small',
      dimension: 1536,
      base_url: null,
      host: 'api.openai.com',
      key: { set: true, hint: 'abc1', updated_at: new Date(Date.now() - 60_000).toISOString() },
      verified: true,
      updated_at: '2026-10-20T10:00:00Z',
    },
    built_in: { model: 'all-MiniLM-L6-v2', dimension: 384 },
    index: {
      searchable: true,
      active_model: 'text-embedding-3-small',
      building: false,
      build: null,
      needs_build: false,
    },
    sign_in_needed: false,
    ...overrides,
  }
}

function renderSite() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/site']}>
        <Routes>
          <Route path="/site" element={<SitePage />} />
          <Route path="/" element={<p>library</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function renderAdmin() {
  const user = userEvent.setup()
  const result = renderSite()
  await screen.findByRole('heading', { name: 'Search by meaning' })
  return { user, ...result }
}

describe('SitePage', () => {
  it('shows the size that was saved, not the model\'s default', async () => {
    const saved = pageSetup()
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': { ...saved, setup: { ...saved.setup, dimension: 768 } },
    })
    renderSite()
    await screen.findByRole('heading', { name: 'Search by meaning' })
    expect(screen.getByLabelText(/Size/)).toHaveValue(768)
  })

  it('says what search by meaning is and what is in use for a page-managed setup', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    const { container } = renderSite()
    await screen.findByRole('heading', { name: 'Search by meaning' })
    expect(screen.getByText(/Finds passages by what they are about/)).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(
      'OpenAI (api.openai.com), text-embedding-3-small, 1536 numbers.',
    )
    expect(screen.getByRole('status')).toHaveTextContent('Passages are found by what they are about.')
    await expectAccessible(container)
  })

  it.each([
    ['server', 'OpenAI (api.openai.com), text-embedding-3-small, 1536 numbers. Set in the server’s settings, so it cannot be changed here.'],
    ['built_in', 'A model on this server (all-MiniLM-L6-v2). Your writing is not sent anywhere for search.'],
    ['off', 'Off. Passages are found by the words they use.'],
  ] as const)('says the %s state plainly', async (source, expected) => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({
        source,
        editable: source !== 'server',
      }),
    })
    renderSite()
    await screen.findByRole('heading', { name: 'Search by meaning' })
    expect(screen.getByRole('note')).toHaveTextContent(expected)
    if (source === 'server') {
      expect(screen.queryByRole('heading', { name: 'Set up search by meaning' })).not.toBeInTheDocument()
    }
  })

  it('offers site administrators the settings and nobody else', async () => {
    mockApi({ 'GET /auth/me': { ...ADMIN_SESSION, platform_admin: false } })
    renderSite()
    expect(await screen.findByRole('heading', { name: 'Site settings' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('Only the site’s administrators can change these settings.')
    expect(screen.getByRole('link', { name: 'Back to your library' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('heading', { name: 'Search by meaning' })).not.toBeInTheDocument()
  })

  it('picking a provider changes the key link, hint and models', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    const { user } = await renderAdmin()

    expect(screen.getByRole('link', { name: 'Get a key from OpenAI' })).toHaveAttribute(
      'href',
      'https://platform.openai.com/api-keys',
    )
    // The saved key is what the field's hint is about while it can be kept.
    expect(screen.getByText(/A key ending in ••••abc1 is saved/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'text-embedding-3-small' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Provider'), 'voyage')
    expect(screen.getByRole('link', { name: 'Get a key from Voyage AI' })).toHaveAttribute(
      'href',
      'https://dash.voyageai.com/api-keys',
    )
    expect(screen.getByText('Starts with pa-.')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'voyage-3-large' })).toBeInTheDocument()
  })

  it('reveals a text field for “Another model…”', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    const { user } = await renderAdmin()
    expect(screen.queryByLabelText('Model name')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Model'), '__custom__')
    expect(screen.getByLabelText('Model name')).toBeInTheDocument()
  })

  it('offers Voyage sizes as a choice and shows a fixed size as text', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    const { user } = await renderAdmin()

    await user.selectOptions(screen.getByLabelText('Provider'), 'voyage')
    const size = screen.getByLabelText('Size')
    expect(size.tagName).toBe('SELECT')
    expect(within(size).getByRole('option', { name: '256 numbers' })).toBeInTheDocument()
    expect(within(size).getByRole('option', { name: '2048 numbers' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Provider'), 'openai')
    await user.selectOptions(screen.getByLabelText('Model'), 'text-embedding-ada-002')
    expect(screen.getByText('1536 numbers. This model’s size is fixed.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Size')).not.toBeInTheDocument()
  })

  it('says a stored key is kept when left empty', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    await renderAdmin()
    expect(screen.getByText(/A key ending in ••••abc1 is saved/)).toBeInTheDocument()
    expect(screen.getByText(/Leave this empty to keep it/)).toBeInTheDocument()
  })

  it('requires a key after the provider is changed', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
    })
    const { user } = await renderAdmin()
    await user.selectOptions(screen.getByLabelText('Provider'), 'voyage')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText('Paste a key from Voyage AI.')).toBeInTheDocument()
    expect(screen.getByLabelText('Key')).toHaveAttribute('aria-invalid', 'true')
  })

  it('places a refused key error on the key field and keeps what was typed', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({
        setup: {
          ...(pageSetup().setup as Record<string, unknown>),
          key: { set: false, hint: null, updated_at: null },
        },
      }),
      'PUT /site/search-setup': problem(400, 'key_refused', 'OpenAI did not accept that key. Check it and paste it again.'),
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-bad')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText('OpenAI did not accept that key. Check it and paste it again.')).toBeInTheDocument()
    expect(screen.getByLabelText('Key')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Key')).toHaveValue('sk-bad')
  })

  it('places a refused model error on the model field', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({
        setup: {
          ...(pageSetup().setup as Record<string, unknown>),
          key: { set: false, hint: null, updated_at: null },
        },
      }),
      'PUT /site/search-setup': problem(400, 'model_refused', 'OpenAI does not have a model called that.'),
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-ok')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText('OpenAI does not have a model called that.')).toBeInTheDocument()
    expect(screen.getByLabelText('Model')).toHaveAttribute('aria-invalid', 'true')
  })

  it('places a size mismatch on the size field', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({
        setup: {
          ...(pageSetup().setup as Record<string, unknown>),
          key: { set: false, hint: null, updated_at: null },
        },
      }),
      'PUT /site/search-setup': problem(400, 'size_mismatch', 'That size does not match the model. Choose one of its sizes.'),
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-ok')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText('That size does not match the model. Choose one of its sizes.')).toBeInTheDocument()
    expect(screen.getByLabelText('Size')).toHaveAttribute('aria-invalid', 'true')
  })

  it('shows the server’s message after a save that checked the key, then offers to build', async () => {
    const api = mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
      'PUT /site/search-setup': {
        check: 'ok',
        message: 'Saved. OpenAI checked the key.',
        state: pageSetup({
          index: {
            searchable: false,
            active_model: null,
            building: false,
            build: null,
            needs_build: true,
          },
        }),
      },
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-new')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved. OpenAI checked the key.')
    expect(screen.getByRole('button', { name: 'Build the search index' })).toBeInTheDocument()
    expect(api.find('PUT', '/site/search-setup')[0].body).toEqual({
      provider: 'openai',
      model: 'text-embedding-3-small',
      dimension: 1536,
      api_key: 'sk-new',
    })
  })

  it('warns calmly when the provider could not be reached to check the key', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
      'PUT /site/search-setup': {
        check: 'unchecked',
        message: 'Saved, but OpenAI could not be reached to check the key. It will be checked again later.',
        state: pageSetup({
          index: {
            searchable: false,
            active_model: null,
            building: false,
            build: null,
            needs_build: true,
          },
        }),
      },
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-new')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Saved, but OpenAI could not be reached to check the key. It will be checked again later.',
    )
    expect(screen.getByRole('status').className).toContain('warn')
    expect(screen.getByRole('button', { name: 'Build the search index' })).toBeInTheDocument()
  })

  it('never shows the key back after saving', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
      'PUT /site/search-setup': {
        check: 'ok',
        message: 'Saved. OpenAI checked the key.',
        state: pageSetup(),
      },
    })
    const { user } = await renderAdmin()
    await user.type(screen.getByLabelText('Key'), 'sk-new')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    await screen.findByText('Saved. OpenAI checked the key.')
    expect(screen.getByLabelText('Key')).toHaveValue('')
  })

  it('asks the administrator to sign in again when the session is too old', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({ sign_in_needed: true }),
    })
    renderSite()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('For your safety, sign in again before changing the site’s key.')
    expect(within(alert).getByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/auth/login?fresh=1&return_to=%2Fsite')
    expect(screen.getByRole('button', { name: 'Check and save' })).toBeDisabled()
  })

  it('asks to sign in again after a refused save', async () => {
    mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
      'PUT /site/search-setup': problem(403, 'sign_in_again', 'Your session ended. Sign in again.'),
    })
    const { user } = await renderAdmin()
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'For your safety, sign in again before changing the site’s key.',
    )
    expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/auth/login?fresh=1&return_to=%2Fsite')
  })

  it('confirms before stopping the service and forgets the key', async () => {
    const api = mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup(),
      'DELETE /site/search-setup': pageSetup({
        source: 'built_in',
        setup: null,
        index: {
          searchable: false,
          active_model: null,
          building: false,
          build: null,
          needs_build: true,
        },
      }),
    })
    const { user } = await renderAdmin()
    await user.click(screen.getByRole('button', { name: 'Stop using a service' }))
    const dialog = await screen.findByRole('dialog', { name: 'Stop using OpenAI?' })
    expect(dialog).toHaveTextContent('The saved key is forgotten.')
    expect(dialog).toHaveTextContent('a model on this server (all-MiniLM-L6-v2)')
    await user.click(within(dialog).getByRole('button', { name: 'Stop using the service' }))
    await waitFor(() => expect(api.find('DELETE', '/site/search-setup')).toHaveLength(1))
    expect(screen.queryByRole('button', { name: 'Stop using a service' })).not.toBeInTheDocument()
  })

  it('shows build progress with a Stop button that cancels', async () => {
    const api = mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({
        index: {
          searchable: false,
          active_model: null,
          building: true,
          build: { done: 5, total: 100, state: 'running' },
          needs_build: false,
        },
      }),
      'POST /site/search-setup/build/cancel': pageSetup(),
    })
    const { user } = await renderAdmin()
    expect(screen.getByText('Building the search index… 5 of 100 passages.')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '5')
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(api.find('POST', '/site/search-setup/build/cancel')).toHaveLength(1))
  })

  it('without a way to keep keys, only a service on this machine can be set up', async () => {
    const api = mockApi({
      'GET /auth/me': ADMIN_SESSION,
      'GET /site/search-providers': PROVIDERS,
      'GET /site/search-setup': pageSetup({ kek_configured: false, setup: null, source: 'off' }),
      'PUT /site/search-setup': { check: 'ok', message: 'Ollama accepted the setup.', state: pageSetup() },
    })
    const user = userEvent.setup()
    renderSite()
    expect(await screen.findByText(/This server cannot keep keys yet/)).toBeInTheDocument()
    expect(screen.getByText(/CREDENTIAL_KEK/)).toBeInTheDocument()
    expect(screen.getByLabelText('Key')).toBeDisabled()

    // A provider that needs a key says why it cannot be saved.
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText(/only a service on this machine can be used/)).toBeInTheDocument()
    expect(api.find('PUT', '/site/search-setup')).toHaveLength(0)

    // A server on this machine needs no key, so it can be saved.
    await user.selectOptions(screen.getByLabelText('Provider'), 'ollama')
    await user.clear(screen.getByLabelText('Address'))
    await user.type(screen.getByLabelText('Address'), 'http://localhost:11434/v1')
    await user.click(screen.getByRole('button', { name: 'Check and save' }))
    expect(await screen.findByText('Ollama accepted the setup.')).toBeInTheDocument()
    expect(api.find('PUT', '/site/search-setup')).toHaveLength(1)
  })

  it('asks a signed-out visitor to sign in', async () => {
    mockApi({ 'GET /auth/me': problem(401, 'unauthenticated', 'Sign in.') })
    renderSite()
    expect(await screen.findByRole('heading', { name: 'Site settings' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/auth/login?return_to=%2Fsite')
  })
})
