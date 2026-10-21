import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// A model on the author's own computer (SCN-002A), in a real browser against a real
// backend that has no model of its own. The stand-in model (scripts/e2e/fake_model.py)
// plays the person's model server, and the real connector (`python -m connector`) runs
// between them: it shows a code, the author types and confirms it under AI assistant,
// chooses the computer's model, and the assistant's suggestion is answered through it.
// Disconnecting stops the connector at once.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const SHOTS = process.env.E2E_SHOTS
const API_URL = `http://127.0.0.1:${Number(process.env.E2E_API_PORT ?? 8010)}`
const NAME = 'Fenwick test laptop'

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/A-${name}.png`, fullPage: true })
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

async function call(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(path, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}

/** Start a child process from the repository root and collect what it prints. */
function start(args: string[]): { child: ChildProcess; output: () => string } {
  let printed = ''
  const child = spawn('uv', ['run', '--locked', 'python', ...args], {
    cwd: '..',
    env: { ...process.env, NO_PROXY: '*' },
  })
  child.stdout?.on('data', (chunk: Buffer) => (printed += chunk.toString()))
  child.stderr?.on('data', (chunk: Buffer) => (printed += chunk.toString()))
  return { child, output: () => printed }
}

async function waitFor<T>(find: () => T | null | undefined, seconds = 60): Promise<T> {
  const deadline = Date.now() + seconds * 1000
  while (Date.now() < deadline) {
    const found = find()
    if (found) return found
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('timed out')
}

test('a model on the author’s own computer is connected with a code, used, and disconnected', async ({
  page,
}) => {
  const stateDir = mkdtempSync(join(tmpdir(), 'novel-connector-'))
  const standIn = start([
    '-c',
    "from scripts.e2e.fake_model import start; import time; s, u = start('127.0.0.1'); print('AT ' + u, flush=True); time.sleep(3600)",
  ])
  let connector: ReturnType<typeof start> | null = null
  try {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto('/')
    await page.getByRole('link', { name: 'Sign in' }).first().click()
    await page.getByRole('link', { name: /Your project/ }).click()
    await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

    const head = async () =>
      (await call(page, 'GET', `/api/v1/projects/${PROJECT}/projection`)).head_revision as number
    const scene = await call(page, 'POST', `/api/v1/projects/${PROJECT}/plan`, {
      kind: 'scene',
      title: 'The Fenwick sluice',
      summary: 'Wren tries to open the Fenwick sluice before the tide turns.',
      expected_revision: await head(),
    })

    // The card, with the steps and nothing connected yet.
    await page.goto(`/projects/${PROJECT}/assistant`)
    const card = page.locator('section[aria-labelledby="computer-heading"]')
    await expect(card.getByRole('heading', { name: 'Use a model on your own computer' })).toBeVisible()
    await expect(card.getByText(/python -m connector --site/)).toBeVisible()
    await expectAccessible(page)
    await shot(page, '1-start-1280')
    await page.setViewportSize({ width: 390, height: 844 })
    await shot(page, '1-start-390')
    await page.setViewportSize({ width: 1280, height: 900 })

    // The connector, between the site and the stand-in, shows a code.
    const modelServer = await waitFor(() => standIn.output().match(/AT (\S+)/)?.[1])
    connector = start([
      '-m',
      'connector',
      '--site',
      API_URL,
      '--model-server',
      modelServer,
      '--name',
      NAME,
      '--state-dir',
      stateDir,
    ])
    const code = await waitFor(() => connector?.output().match(/Code: ([A-Z]{4}-[A-Z]{4})/)?.[1])

    await card.getByLabel('Code from the connector').fill(code.toLowerCase())
    await card.getByRole('button', { name: 'Check code' }).click()
    const confirm = card.getByRole('group', { name: 'Confirm the computer' })
    await expect(confirm.getByText(`Connect “${NAME}”?`)).toBeVisible()
    await expect(confirm.getByText(/If someone sent you this code, turn it down/)).toBeVisible()
    await expectAccessible(page)
    await shot(page, '2-confirm-1280')
    await confirm.getByRole('button', { name: 'Connect this computer' }).click()
    await expect(card.getByText(`Connected “${NAME}”.`, { exact: false })).toBeVisible()

    // Once the connector has its credential, it lists the stand-in's model.
    const model = card.getByLabel(`Model on “${NAME}”`)
    await expect(model).toBeVisible({ timeout: 30_000 })
    await expect(model).toHaveValue('e2e-model')
    await expect(card.getByText(/LM Studio · 1 model/)).toBeVisible()
    await card.getByRole('button', { name: 'Use for the assistant' }).click()
    await expect(card.getByText('In use')).toBeVisible()
    await expect(card.getByText(`The assistant is using e2e-model on “${NAME}”.`)).toBeVisible()
    await expectAccessible(page)
    await shot(page, '3-in-use-1280')
    await page.getByRole('button', { name: 'Switch to dark theme' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await expectAccessible(page)
    await shot(page, '3-in-use-1280-dark')
    await page.getByRole('button', { name: 'Switch to light theme' }).click()
    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await expect(card.getByText('In use')).toBeVisible()
    await expectAccessible(page)
    await shot(page, '3-in-use-390')
    await page.setViewportSize({ width: 1280, height: 900 })

    // The assistant's suggestion is answered by the computer: this site has no model.
    await page.goto(`/projects/${PROJECT}/plan`)
    const row = page.locator(`#plan-${scene.id}`)
    await row.getByRole('button', { name: 'Suggest what this scene is for' }).click()
    const offer = row.getByRole('group', { name: 'Suggest what this scene is for' })
    await offer.getByRole('button', { name: 'Ask the assistant' }).click()
    await expect(offer.getByRole('link', { name: 'See the suggestion in Review' })).toBeVisible({
      timeout: 60_000,
    })
    // Turned down, so the shared story's Review is left as it was.
    const suggestion = (await offer.getByRole('link', { name: 'See the suggestion in Review' }).getAttribute('href'))!
      .split('/')
      .pop()!
    await call(page, 'POST', `/api/v1/projects/${PROJECT}/mutations/${suggestion}/reject`, {})
    await waitFor(() => connector?.output().includes('POST /chat/completions: answered 200'))
    // The connector says what it did, never the words it carried.
    expect(connector.output()).not.toContain('Fenwick sluice')

    // Stop using it, then disconnect it: the connector stops at once.
    await page.goto(`/projects/${PROJECT}/assistant`)
    await card.getByRole('button', { name: 'Stop using this computer' }).click()
    await expect(card.getByText(`The assistant no longer uses “${NAME}”.`)).toBeVisible()
    await card.getByRole('button', { name: `Disconnect “${NAME}”` }).click()
    const dialog = page.getByRole('dialog', { name: `Disconnect “${NAME}”?` })
    await expect(dialog.getByText(/The connector on it stops working at once/)).toBeVisible()
    await expectAccessible(page)
    await shot(page, '4-disconnect-1280')
    await dialog.getByRole('button', { name: 'Disconnect computer' }).click()
    await expect(card.getByText('Disconnected computers (1)', { exact: false })).toBeVisible()
    const exited = await waitFor(() => (connector?.child.exitCode !== null ? connector : null), 60)
    expect(exited.child.exitCode).toBe(3)
    expect(exited.output()).toContain('This computer was disconnected from the site')
  } finally {
    // Leave the shared story's assistant as it was: no computer chosen.
    await call(page, 'DELETE', '/api/v1/ai/connector/use').catch(() => undefined)
    connector?.child.kill()
    standIn.child.kill()
    rmSync(stateDir, { recursive: true, force: true })
  }
})
