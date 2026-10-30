import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Where an author's writing goes to be searchable by meaning, on the Assistant page. The
// site's own setting is read first; then the answer for a site that sends writing to an
// embedding service is supplied by the test, to see how that reads.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/search-where-${name}.png`, fullPage: true })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([])
}

test('the assistant page says where writing goes to be searched by meaning', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const site = await (await page.request.get('/api/v1/ai/search')).json()
  await page.goto(`/projects/${PROJECT}/assistant`)
  const card = page.getByRole('region', { name: 'Search by meaning' })
  await expect(card).toBeVisible()
  const said = {
    server: 'Your writing is not sent anywhere for it.',
    service: 'This site sends your writing to',
    off: 'Search by meaning is off on this site',
  }[site.kind as 'server' | 'service' | 'off']
  await expect(card.getByRole('note')).toContainText(said)
  await expectAccessible(page)
  await shot(page, '01-this-site')

  // A site that sends writing to an embedding service, not yet switched on.
  await page.route('**/api/v1/ai/search', (route) =>
    route.fulfill({
      json: { kind: 'service', name: 'OpenAI', host: 'api.openai.com', in_use: false },
    }),
  )
  await page.reload()
  await expect(card.getByRole('note')).toContainText(
    'This site sends your writing to OpenAI (api.openai.com) to make it searchable.',
  )
  await expect(card.getByRole('note')).toContainText('not your key')
  await expect(card.getByRole('note')).toContainText('not switched on yet')
  await expectAccessible(page)
  await shot(page, '02-a-service')
})
