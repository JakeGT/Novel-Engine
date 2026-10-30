import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Name answers that hold only in part of the story (CTX-016), in a real browser against a
// real backend, with no AI provider: a marked name in a scene is answered "only from this
// scene on"; an earlier scene still asks; the names page shows where the answer holds and
// the passage it was answered from.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/A-${name}.png`, fullPage })
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

test('a name answered from one scene on holds there, and the names page says where', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const brin = (
    await call(page, 'POST', '/entities', { type: 'Character', name: 'Brin Halloway', expected_revision: await head(page) })
  ).id as string
  const texts: Record<string, string> = {}
  for (const title of ['The pier at dawn', 'The pier at noon']) {
    const scene = await call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      refs: [{ entity_id: brin, role: 'point_of_view' }],
      expected_revision: await head(page),
    })
    const saved = await call(page, 'PUT', `/plan/${scene.id}/studio/text`, {
      content: `Brin Halloway waited. Then @"the gull-keeper" called across the pier.`,
      base_version_id: null,
    })
    texts[title] = saved.text_id as string
  }

  // At noon: who is "the gull-keeper"? Brin, but only from this scene on.
  await page.goto(`/projects/${PROJECT}/texts/${texts['The pier at noon']}`)
  const question = page.getByRole('region', { name: 'Who is this?' })
  await expect(question).toContainText('“the gull-keeper”')
  const where = question.getByLabel('Where it holds')
  await expect(where).toHaveValue('everywhere')
  await where.selectOption({ label: 'Only from this scene on' })
  await expect(question.getByLabel('From')).toHaveValue(/.+/)
  await expect(question.getByLabel('From').locator('option:checked')).toHaveText(/\(this scene\)/)
  await expectAccessible(page)
  await shot(page, 'text-question-limit')
  await question.getByLabel('Someone or something else').selectOption({ label: 'Brin Halloway (Character)' })
  await question.getByRole('button', { name: 'That one' }).click()
  await expect(page.getByRole('heading', { name: 'Who is this?' })).toHaveCount(0)

  // At dawn, before that scene, it is still a question.
  await page.goto(`/projects/${PROJECT}/texts/${texts['The pier at dawn']}`)
  await expect(page.getByRole('region', { name: 'Who is this?' })).toContainText('“the gull-keeper”')

  // The names page says where the answer holds and keeps the passage it came from.
  await page.getByRole('link', { name: 'Every name you have answered' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Names' })).toBeVisible()
  const card = page.getByRole('region', { name: '“the gull-keeper”' })
  await expect(card).toContainText('Means Brin Halloway · Only from “The pier at noon” on')
  await expect(card.locator('mark')).toHaveText('@"the gull-keeper"')
  await expect(card.getByText('Still reads this way')).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'names-page')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(250)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await shot(page, 'names-page-390')
  await page.goto(`/projects/${PROJECT}/texts/${texts['The pier at dawn']}`)
  await page.getByRole('region', { name: 'Who is this?' }).getByLabel('Where it holds').selectOption({ label: 'Only when seen by a character' })
  await expectAccessible(page)
  await shot(page, 'text-question-390')
})
