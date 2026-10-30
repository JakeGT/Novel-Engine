import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Incremental checks and what becomes of findings, in a real browser against a real
// backend, with no AI provider (CON-007, CON-001, CON-016): each save keeps a record that
// says which checks were worked out again and which read nothing that had changed; a
// finding a later check found again is marked so; dismissing a check shows up in how
// often each check is dismissed; and an advisory finding on the review screen is
// dismissed for that proposal and brought back.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/B-${name}.png`, fullPage })
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

const STOCK = '“palpable” is stock phrasing that readers often notice.'
const FIRST =
  'Pell Ashdown climbed the Gull Steps in the rain. The fear in the air was palpable.\n\nNobody waited at the top.'

test('checks are worked out only where something changed, and what became of each finding is kept', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['pell', 'Character', 'Pell Ashdown'],
    ['steps', 'Location', 'Gull Steps'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Up the Gull Steps',
    summary: 'Pell climbs to meet someone who does not come.',
    refs: [
      { entity_id: ids.pell, role: 'point_of_view' },
      { entity_id: ids.steps, role: 'place' },
    ],
    expected_revision: await head(page),
  })

  // The first save works every check out.
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill(FIRST)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()
  const history = page.getByRole('region', { name: 'Check history' })
  await expect(history.locator('.check-run')).toHaveCount(1)
  await expect(history.locator('.check-run').first()).toContainText(/All \d+ checks were worked out\./)

  // Another sentence changes: the checks that read the words are worked out again, the
  // continuity checks (which read only the story) are not; the older record's stock phrase
  // was found again by the newer one.
  await editor.fill(FIRST.replace('Nobody waited at the top.', 'Nobody came, and the lamps went out.'))
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 2/)).toBeVisible()
  await expect(history.locator('.check-run')).toHaveCount(2)
  const newest = history.locator('.check-run').first()
  await expect(newest).toContainText(/checks were worked out again; continuity in story time read nothing that had changed/)
  const older = history.locator('.check-run').nth(1)
  await expect(older).toContainText('found again later')
  await older.getByText(/^Show the/).click()
  await expect(older).toContainText('Found again by a later check')
  await shot(page, '1-history-worked-out-again')
  await expectAccessible(page)

  // Dismissed as not a problem: it counts in how often that check is dismissed.
  const checks = page.getByRole('region', { name: 'Checks', exact: true })
  await checks.getByRole('button', { name: `Dismiss: ${STOCK}` }).click()
  const form = checks.getByRole('group', { name: `Dismiss: ${STOCK}` })
  await form.getByRole('button', { name: 'Not a problem' }).click()
  await form.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(checks).toContainText(/Dismissed as\s*Not a problem/)
  await page.reload()
  const calibration = page.getByRole('region', { name: 'How often you dismiss each check' })
  await expect(calibration.locator('li', { hasText: 'Stock phrasing' })).toContainText('Too few to say')
  await calibration.scrollIntoViewIfNeeded()
  await shot(page, '2-how-often-dismissed', false)
  await expectAccessible(page)

  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await history.scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await shot(page, '3-history-dark', false)
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Switch to light' }).click()

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await page.getByRole('region', { name: 'Check history' }).scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await shot(page, '4-history-phone', false)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1280, height: 900 })

  // On the review screen: an advisory finding is dismissed for this proposal, and back.
  const proposal = await call(page, 'POST', '/mutations', {
    operations: [{ op: 'create_entity', id: 'tmp:a', type: 'Faction', name: 'gull steps' }],
    expected_revision: await head(page),
    origin: 'model',
  })
  await page.goto(`/projects/${PROJECT}/review/${proposal.id}`)
  const change = page.locator('li.change').first()
  await expect(change).toContainText('Worth a look:')
  const dismiss = change.getByRole('button', { name: /^Dismiss: / })
  await dismiss.click()
  const reviewForm = change.getByRole('group', { name: /^Dismiss: / })
  await expect(reviewForm).toContainText('For these changes only.')
  await reviewForm.getByRole('button', { name: 'Not a problem' }).click()
  await shot(page, '5-review-dismiss-form', false)
  await expectAccessible(page)
  await reviewForm.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(change).toContainText('Dismissed (Not a problem):')
  await shot(page, '6-review-dismissed', false)
  await expectAccessible(page)
  await change.getByRole('button', { name: /^Bring back: / }).click()
  await expect(change).toContainText('Worth a look:')

  await page.setViewportSize({ width: 390, height: 844 })
  await change.getByRole('button', { name: /^Dismiss: / }).click()
  await page.waitForTimeout(300)
  await shot(page, '7-review-dismiss-phone', false)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})
