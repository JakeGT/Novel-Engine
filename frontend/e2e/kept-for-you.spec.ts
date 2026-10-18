import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Keep the safe ones for me (AST-EXP-015) with no AI provider: the rules can be read and
// chosen by hand. New entries suggested from elsewhere and approved by hand count toward
// switching a kind on; the review queue offers it once there are enough; the rules save at
// once, and switching off is always allowed.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.waitForTimeout(450)
  await page.screenshot({ path: `${SHOTS}/d-kept-nomodel-${name}.png`, fullPage: true })
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

test.afterEach(async ({ page }) => {
  await call(page, 'PUT', '/kept-for-you/settings', { kinds: [], allow_clients: false })
})

test('the rules are earned by approving by hand, offered, and chosen without a model', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  // Off by default: a kind not yet approved by hand three times cannot be switched on.
  await page.goto(`/projects/${PROJECT}/review/kept`)
  await expect(page.getByRole('heading', { level: 1, name: 'Kept for you' })).toBeVisible()
  await expect(page.getByText('Nothing has been kept for you yet')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /New facts that change nothing else/ })).toBeDisabled()
  await expect(page.getByText('Undoing earlier changes')).toBeVisible()
  await expectAccessible(page)

  // Three suggested new entries (as the assistant would make them), the last approved in
  // the review screen.
  const stamp = Date.now().toString(36)
  let last = ''
  for (const name of ['Hale', 'Iver', 'Juno']) {
    const proposed = await call(page, 'POST', '/mutations', {
      origin: 'model',
      expected_revision: await head(page),
      operations: [{ op: 'create_entity', id: 'tmp:n', type: 'Character', name: `${name} ${stamp}` }],
    })
    await call(page, 'POST', `/mutations/${proposed.id}/submit`, { note: 'suggested' })
    last = proposed.id
    if (name !== 'Juno') {
      await call(page, 'POST', `/mutations/${proposed.id}/approve`, {})
      await call(page, 'POST', `/mutations/${proposed.id}/commit`, {})
    }
  }
  await page.goto(`/projects/${PROJECT}/review/${last}`)
  await page.getByRole('button', { name: 'Approve and add to your story' }).click()
  await expect(page.getByText(/Added to your story as version/)).toBeVisible()

  // Now the review queue offers it.
  await page.goto(`/projects/${PROJECT}/review`)
  const offer = page.getByText(/new entries from the assistant by hand/)
  await expect(offer).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'offer')
  await page.getByRole('link', { name: 'Choose what to keep' }).click()

  const rule = page.getByRole('checkbox', { name: /New entries/ })
  await expect(rule).toBeEnabled()
  await rule.click()
  await expect(rule).toBeChecked()
  await expect(page.getByText('“New entries” will be kept for you from now on.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('checkbox', { name: /New entries/ })).toBeChecked()
  await expectAccessible(page)
  await shot(page, 'rules')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('checkbox', { name: /New entries/ })).toBeChecked()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await shot(page, 'phone')
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.getByRole('button', { name: 'Switch it all off' }).click()
  await expect(page.getByText('Switched off. Every suggestion waits for you again.')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /New entries/ })).not.toBeChecked()
})
