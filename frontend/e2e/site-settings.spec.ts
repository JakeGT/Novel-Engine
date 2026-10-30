import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Site settings (SSP-001): an administrator picks the provider writing is sent to for
// search by meaning, pastes a key, has it checked, and builds the search index. The
// real backend may not have these endpoints yet, so the setup, providers, save and build
// are supplied by `page.route`; only sign-in runs against the real local-mode backend.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const SHOTS = process.env.E2E_SHOTS

interface Setup {
  source: 'page' | 'server' | 'built_in' | 'off'
  editable: boolean
  kek_configured: boolean
  setup: {
    provider: string
    provider_label: string
    model: string
    dimension: number
    base_url: string | null
    host: string
    key: { set: boolean; hint: string | null; updated_at: string | null }
    verified: boolean | null
    updated_at: string
  } | null
  built_in: { model: string; dimension: number } | null
  index: {
    searchable: boolean
    active_model: string | null
    building: boolean
    build: { done: number; total: number; state: 'running' | 'waiting' | 'failed' } | null
    needs_build: boolean
  }
  sign_in_needed: boolean
}

const PROVIDERS = [
  {
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
    ],
    dimension_choices: null,
  },
  {
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
  },
]

function pageSetup(): Setup {
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
      key: { set: false, hint: null, updated_at: null },
      verified: true,
      updated_at: '2026-10-20T10:00:00Z',
    },
    built_in: { model: 'all-MiniLM-L6-v2', dimension: 384 },
    index: {
      searchable: false,
      active_model: null,
      building: false,
      build: null,
      needs_build: true,
    },
    sign_in_needed: false,
  }
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/site-settings-${name}.png`, fullPage: true })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([])
}

test('an administrator sets up search by meaning, builds the index, and is refused a bad key', async ({
  page,
}) => {
  // The real local-mode session has no platform_admin yet; give the page one.
  await page.route('**/auth/me', async (route) => {
    const response = await route.fetch()
    const body = await response.json().catch(() => null)
    if (body && typeof body === 'object') {
      ;(body as Record<string, unknown>).platform_admin = true
      await route.fulfill({ response, json: body })
    } else {
      await route.fulfill({ response })
    }
  })

  await page.route('**/api/v1/site/search-providers', (route) => route.fulfill({ json: PROVIDERS }))

  let setup: Setup = pageSetup()
  await page.route('**/api/v1/site/search-setup', async (route) => {
    const method = route.request().method()
    if (method === 'GET') {
      // The poll while building flips the index to built.
      if (setup.index.building) {
        setup = {
          ...setup,
          index: {
            searchable: true,
            active_model: 'text-embedding-3-small',
            building: false,
            build: null,
            needs_build: false,
          },
        }
      }
      return route.fulfill({ json: setup })
    }
    if (method === 'PUT') {
      const body = route.request().postDataJSON()
      if (body.api_key === 'sk-refused') {
        return route.fulfill({
          status: 400,
          json: {
            type: 'about:blank',
            title: 'key_refused',
            status: 400,
            code: 'key_refused',
            detail: 'OpenAI did not accept that key. Check it and paste it again.',
            field_errors: {},
          },
        })
      }
      setup = {
        ...setup,
        setup: {
          ...setup.setup!,
          key: { set: true, hint: 'ab12', updated_at: new Date().toISOString() },
        },
        index: {
          searchable: false,
          active_model: null,
          building: false,
          build: null,
          needs_build: true,
        },
      }
      return route.fulfill({
        json: { check: 'ok', message: 'Saved. OpenAI checked the key.', state: setup },
      })
    }
    return route.fulfill({ status: 405, json: { detail: 'unexpected method' } })
  })

  await page.route('**/api/v1/site/search-setup/build', async (route) => {
    setup = {
      ...setup,
      index: {
        searchable: false,
        active_model: null,
        building: true,
        build: { done: 5, total: 100, state: 'running' },
        needs_build: false,
      },
    }
    return route.fulfill({ status: 202, json: setup })
  })

  // Sign in, then open Site settings from the account area in the header.
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await expect(page.getByRole('link', { name: 'Site settings' })).toBeVisible()
  await shot(page, '01-library-header')

  await page.getByRole('link', { name: 'Site settings' }).click()
  await expect(page.getByRole('heading', { name: 'Site settings' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Search by meaning', exact: true })).toBeVisible()
  await expectAccessible(page)
  await shot(page, '02-page')

  // Choose OpenAI, type a key, and save; the provider is asked to check it first.
  const form = page.getByRole('region', { name: 'Set up search by meaning' })
  await form.getByLabel('Provider').selectOption('openai')
  await form.getByLabel('Key').fill('sk-ok')
  await expect(
    form.getByText('OpenAI is asked to read one word with this setup before anything is kept.'),
  ).toBeVisible()
  await form.getByRole('button', { name: 'Check and save' }).click()
  await expect(form.getByText('Saved. OpenAI checked the key.')).toBeVisible()
  await expect(form.getByLabel('Key')).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Build the search index' })).toBeVisible()
  await expectAccessible(page)
  await shot(page, '03-saved')

  // Build the index: progress, then it is searchable.
  await page.getByRole('button', { name: 'Build the search index' }).click()
  await expect(page.getByRole('progressbar')).toBeVisible()
  await expect(page.getByText(/Building the search index/)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '04-building')
  await expect(page.getByText('Passages are found by what they are about.')).toBeVisible({ timeout: 15_000 })
  await expectAccessible(page)
  await shot(page, '05-built')

  // A refused key is reported beside the key field, and what was typed stays.
  await form.getByLabel('Key').fill('sk-refused')
  await form.getByRole('button', { name: 'Check and save' }).click()
  await expect(form.getByText('OpenAI did not accept that key. Check it and paste it again.')).toBeVisible()
  await expect(form.getByLabel('Key')).toHaveValue('sk-refused')
  await expectAccessible(page)
  await shot(page, '06-refused')

  // When the server's own settings decide, the page is read-only.
  setup = {
    ...pageSetup(),
    source: 'server',
    editable: false,
    setup: {
      ...pageSetup().setup!,
      key: { set: true, hint: 'ab12', updated_at: new Date().toISOString() },
    },
    index: {
      searchable: true,
      active_model: 'text-embedding-3-small',
      building: false,
      build: null,
      needs_build: false,
    },
  }
  await page.reload()
  await expect(page.getByRole('region', { name: 'Search by meaning', exact: true })).toContainText(
    'Set in the server’s settings, so it cannot be changed here.',
  )
  await expect(page.getByRole('region', { name: 'Set up search by meaning' })).toHaveCount(0)
  await expectAccessible(page)
  await shot(page, '07-server-settings')
})
