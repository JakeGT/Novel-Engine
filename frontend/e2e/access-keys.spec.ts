import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// Other assistants (QRY-003), in a real browser against a real backend, with no AI provider:
// the author makes an access key under People, sees it once with settings to paste into an
// assistant, the assistant reads the story through the MCP endpoint and suggests a change,
// the suggestion waits in Review, and a withdrawn key stops working at once.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/access-keys-${name}.png`, fullPage })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(
    results.violations.map(
      (violation) =>
        `${violation.id}: ${violation.help} [${violation.nodes.map((node) => node.target.join(' ')).join(' | ')}]`,
    ),
  ).toEqual([])
}

let rpcId = 0

async function mcp(request: APIRequestContext, key: string, method: string, params: object = {}) {
  rpcId += 1
  return request.post('/mcp', {
    data: { jsonrpc: '2.0', id: rpcId, method, params },
    headers: {
      Authorization: `Bearer ${key}`,
      Accept: 'application/json, text/event-stream',
      'MCP-Protocol-Version': '2025-11-25',
    },
  })
}

async function tool(request: APIRequestContext, key: string, name: string, args: object = {}) {
  const response = await mcp(request, key, 'tools/call', { name, arguments: args })
  expect(response.status(), await response.text()).toBe(200)
  return (await response.json()).result
}

async function makeKey(page: Page, name: string, suggest: boolean): Promise<string> {
  const form = page.getByRole('form', { name: 'Make an access key' })
  await form.getByLabel('Name', { exact: true }).fill(name)
  if (suggest) await form.getByLabel(/Read and suggest changes/).check()
  await form.getByRole('button', { name: 'Make access key' }).click()
  const key = await page.getByLabel('Access key', { exact: true }).inputValue()
  expect(key).toMatch(/^nvlpat_[0-9A-Za-z]{49}$/)
  return key
}

test('an assistant reads the story with an access key, suggests, and stops when withdrawn', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  await page.goto(`/projects/${PROJECT}/people`)
  const section = page.getByRole('region', { name: 'Other assistants' })
  await expect(section).toBeVisible()
  await expect(section.getByLabel('Read only')).toBeChecked()
  await shot(page, 'empty')

  const reader = await makeKey(page, 'Claude on my laptop', false)
  const made = page.locator('.access-key-made')
  await expect(made).toContainText('shown only once')
  const settings = await made.getByLabel('Settings for your assistant').inputValue()
  expect(JSON.parse(settings).mcpServers['novel-engine']).toMatchObject({
    type: 'http',
    url: expect.stringMatching(/\/mcp$/),
    headers: { Authorization: `Bearer ${reader}` },
  })
  await expect(section.getByRole('listitem').filter({ hasText: 'Claude on my laptop' })).toContainText(
    `ends in …${reader.slice(-4)}`,
  )
  await expectAccessible(page)
  await shot(page, 'made')

  // The assistant's side: the same address the settings name.
  const started = await mcp(page.request, reader, 'initialize', {
    protocolVersion: '2025-11-25',
    capabilities: {},
    clientInfo: { name: 'e2e', version: '1' },
  })
  expect((await started.json()).result.serverInfo.name).toBe('novel-engine')
  const tools = (await (await mcp(page.request, reader, 'tools/list')).json()).result.tools
  expect(tools.map((item: { name: string }) => item.name)).not.toContain('suggest_changes')
  const stories = await tool(page.request, reader, 'list_stories')
  expect(stories.structuredContent.stories[0].id).toBe(PROJECT)
  const refused = await tool(page.request, reader, 'suggest_changes', {
    changes: [{ op: 'create_entity', id: 'tmp:x', type: 'Character', name: 'X' }],
  })
  expect(refused.isError).toBe(true)

  // A key that may suggest: its suggestion waits in Review and changes nothing.
  const suggester = await makeKey(page, 'Suggests changes', true)
  const suggested = await tool(page.request, suggester, 'suggest_changes', {
    changes: [{ op: 'create_entity', id: 'tmp:wren', type: 'Character', name: 'Wren Hollis' }],
    note: 'Wren is named in the second chapter.',
  })
  expect(suggested.isError, JSON.stringify(suggested)).toBe(false)
  const found = await tool(page.request, reader, 'search_entries', { query: 'Wren' })
  expect(found.structuredContent.entries).toEqual([])

  await page.goto(`/projects/${PROJECT}/review`)
  await expect(page.getByText('Changes from a connected assistant').first()).toBeVisible()
  await shot(page, 'review')

  // Use is counted by what was asked, never what was read.
  await page.goto(`/projects/${PROJECT}/people`)
  const row = section.getByRole('listitem').filter({ hasText: 'Claude on my laptop' })
  await expect(row).toContainText('Last used')
  await row.getByRole('button', { name: 'Show use of Claude on my laptop' }).click()
  await expect(row.getByRole('table')).toContainText('Listed stories')

  await row.getByRole('button', { name: 'Withdraw Claude on my laptop' }).click()
  const dialog = page.getByRole('dialog', { name: 'Withdraw “Claude on my laptop”?' })
  await expect(dialog.getByRole('button', { name: 'Keep it' })).toBeFocused()
  await page.waitForTimeout(400)
  await shot(page, 'withdraw', false)
  await dialog.getByRole('button', { name: 'Withdraw key' }).click()
  await expect(page.getByText('“Claude on my laptop” no longer works.')).toBeVisible()
  await expect(row).toContainText('Withdrawn')
  const gone = await mcp(page.request, reader, 'tools/list')
  expect(gone.status()).toBe(401)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(section).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'phone')
})
