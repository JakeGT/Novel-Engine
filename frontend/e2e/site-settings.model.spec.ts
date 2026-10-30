import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { modelPort } from '../playwright.model.config'

// Site settings against the real server (SSP-001): the site's search by meaning is
// pointed at "your own" service — the stand-in model server, which also answers
// /embeddings — checked, saved, built and switched on; then given up again, so the
// other browser tests keep the site as it was.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/site-settings-real-${name}.png`, fullPage: true })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([])
}

test('the site is pointed at its own embedding service, checked, built and given up', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: 'Site settings' }).first().click()
  await expect(page.getByRole('heading', { name: 'Site settings' })).toBeVisible()
  const form = page.getByRole('region', { name: 'Set up search by meaning' })
  await expect(form).toBeVisible()

  await form.getByLabel('Provider').selectOption('custom')
  await form.getByLabel('Model').fill('e2e-embed')
  await form.getByLabel('Size').fill('64')
  await form.getByLabel('Address').fill(`http://127.0.0.1:${modelPort}/v1`)
  await expectAccessible(page)
  await shot(page, '01-filled')

  await form.getByRole('button', { name: 'Check and save' }).click()
  await expect(form.getByRole('status')).toContainText('accepted the setup')
  const summary = page.getByRole('region', { name: 'Search by meaning' })
  await expect(summary.getByRole('note')).toContainText('e2e-embed, 64 numbers')
  await expectAccessible(page)
  await shot(page, '02-saved')

  await summary.getByRole('button', { name: 'Build the search index' }).click()
  await expect(summary.getByText('Passages are found by what they are about.')).toBeVisible({
    timeout: 60_000,
  })
  await expectAccessible(page)
  await shot(page, '03-built')

  // Authors are told where their writing goes.
  const where = await (await page.request.get('/api/v1/ai/search')).json()
  expect(where.kind).toBe('server')

  // Give it up again: the dialog says what happens first.
  await page.getByRole('button', { name: 'Stop using a service' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Passages are found by the words they use')
  await shot(page, '04-stop-dialog')
  await dialog.getByRole('button', { name: 'Stop using the service' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop using a service' })).toHaveCount(0)
  await shot(page, '05-given-up')
})
