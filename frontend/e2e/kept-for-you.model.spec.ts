import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Keep the safe ones for me (AST-EXP-015), in a real browser against the local stand-in
// model: the author approves three new entries from the assistant by hand, is offered
// keeping suggestions like them, switches "New entries" on, and the next new entry the
// assistant suggests goes into the story without asking, named as kept under that rule.
// It is listed under "Kept for you", where Undo suggests the undo for review; switching
// the rule off makes the next one wait again.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (!SHOTS) return
  await page.waitForTimeout(450)
  await page.screenshot({ path: `${SHOTS}/d-kept-${name}.png`, fullPage })
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
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

async function ask(page: Page, name: string) {
  return call(page, 'POST', '/assist/changes', {
    instruction: `Add a character called ${name}.`,
    expected_revision: await head(page),
  })
}

// The rules are the story's own: switch them off so later tests start from the defaults.
test.afterEach(async ({ page }) => {
  await call(page, 'PUT', '/kept-for-you/settings', { kinds: [], allow_clients: false })
})

test('new entries are kept for the author once they choose it, listed with an Undo', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  // Three new entries from the assistant, approved by hand.
  for (const name of ['Nell Ashdown', 'Odo Marsh', 'Piet Vane']) {
    const asked = await ask(page, name)
    expect(asked.status).toBe('needs_review')
    await call(page, 'POST', `/mutations/${asked.mutation_id}/approve`, {})
    await call(page, 'POST', `/mutations/${asked.mutation_id}/commit`, {})
  }

  // The review queue offers it now, and not before.
  await page.goto(`/projects/${PROJECT}/review`)
  await expect(page.getByText(/You have approved 3 new entries from the assistant by hand/)).toBeVisible()
  await shot(page, 'offer')
  await page.getByRole('link', { name: 'Choose what to keep' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Kept for you' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /Details for entries that have none/ })).toBeDisabled()
  await page.getByRole('checkbox', { name: /New entries/ }).click()
  await expect(page.getByRole('checkbox', { name: /New entries/ })).toBeChecked()
  await expect(page.getByText('“New entries” will be kept for you from now on.')).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'rules')

  // The next new entry the assistant suggests goes straight in, saying why.
  await page.getByRole('button', { name: 'Change anything' }).click()
  const dialog = page.getByRole('dialog', { name: 'Change anything' })
  await dialog.getByLabel('What should change?').fill('Add a character called Rosa Fenn.')
  await dialog.getByRole('button', { name: 'Show me what would change' }).click()
  await expect(page.getByRole('note').filter({ hasText: 'Kept for you under your rule “New entries”.' })).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'kept-change')

  // The review queue counts it until the author looks.
  await page.goto(`/projects/${PROJECT}/review`)
  await expect(page.getByText('1 suggestion was kept for you since you last looked.')).toBeVisible()
  await page.getByRole('link', { name: 'See what was kept' }).click()
  const row = page.locator('li.kept-item').filter({ hasText: 'Add character “Rosa Fenn”' })
  await expect(row).toContainText('Kept for you under your rule “New entries”')
  await expectAccessible(page)
  await shot(page, 'list')

  // History says so too.
  await page.goto(`/projects/${PROJECT}/history`)
  await expect(page.getByText(/kept for you under your rule “New entries”/).first()).toBeVisible()

  // Undo suggests the undo; nothing is undone until the author approves it.
  await page.goto(`/projects/${PROJECT}/review/kept`)
  await page.locator('li.kept-item').filter({ hasText: 'Rosa Fenn' }).getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Undo suggested and waiting for you.')).toBeVisible()
  await shot(page, 'undo')
  await page.getByRole('link', { name: 'Review the undo' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Restore an earlier version')

  // At phone width.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/review/kept`)
  await expect(page.getByRole('heading', { level: 1, name: 'Kept for you' })).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(390)
  await shot(page, 'phone')
  await page.setViewportSize({ width: 1280, height: 900 })

  // Switched off, the next one waits, and says why it was not kept.
  await page.getByRole('button', { name: 'Switch it all off' }).click()
  await expect(page.getByText('Switched off. Every suggestion waits for you again.')).toBeVisible()
  const waiting = await ask(page, 'Silas Brook')
  expect(waiting.status).toBe('needs_review')
})
