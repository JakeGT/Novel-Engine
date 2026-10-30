import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The plan's own checks, in a real browser against a real backend, with no AI provider:
// a fact shown before the scene where it is meant to come out is flagged, a required
// scene condition that is not true is flagged while a preferred one is a note, and the
// promises the plan makes the reader are listed with how long the reader waits
// (STY-005, STY-007).
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/plan-checks-${name}.png`, fullPage })
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
  return response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

test('the plan flags what comes too early or is not ready, and lists what the reader is owed', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['juno', 'Character', 'Juno Marsh'],
    ['light', 'Location', 'The Lighthouse'],
    ['company', 'Faction', 'The Ferry Company'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const fact = async (predicate: string, target: string) =>
    (
      await call(page, 'POST', '/facts', {
        proposition: {
          subject_id: ids.juno,
          predicate,
          object: { kind: 'entity_ref', entity_id: ids[target], project_id: PROJECT },
        },
        expected_revision: await head(page),
      })
    ).id as string
  ids.atLight = await fact('LOCATED_AT', 'light')
  ids.inCompany = await fact('MEMBER_OF', 'company')

  const add = async (body: Record<string, unknown>) =>
    (await call(page, 'POST', '/plan', { ...body, expected_revision: await head(page) })).id as string
  ids.sank = await add({ kind: 'thread', title: 'Who sank the ferry?' })
  ids.leave = await add({ kind: 'thread', title: 'Will Juno leave the island?' })
  const pov = [{ entity_id: ids.juno, role: 'point_of_view' }]
  ids.warning = await add({ kind: 'scene', title: 'Storm Warning', refs: pov, sets_up: [ids.sank] })
  ids.wreck = await add({ kind: 'scene', title: 'The Wreck', refs: pov, sets_up: [ids.leave] })
  ids.lamplight = await add({ kind: 'scene', title: 'Lamplight', refs: pov, pays_off: [ids.sank] })
  await call(page, 'PUT', `/plan/${ids.warning}/reveals`, {
    reveals: [{ fact_id: ids.inCompany, how: 'shown' }],
    expected_revision: await head(page),
  })

  await page.goto(`/projects/${PROJECT}/plan`)
  await expect(page.getByRole('heading', { level: 1, name: 'Story plan' })).toBeVisible()

  // The scene where her membership is meant to come out.
  const lamplight = page.locator(`#plan-${ids.lamplight}`)
  await lamplight.getByText('Move, plan, remove').click()
  await lamplight.getByRole('button', { name: 'What the reader learns' }).click()
  const reveals = page.getByRole('dialog', { name: 'What the reader learns in “Lamplight”' })
  const row = reveals.getByRole('listitem').filter({ hasText: 'The Ferry Company' })
  await row.getByRole('checkbox').check()
  await row.getByRole('combobox').selectOption('main')
  await expectAccessible(page)
  await shot(page, 'reveal-dialog', false)
  await reveals.getByRole('button', { name: 'Save what the reader learns' }).click()
  await expect(reveals).toHaveCount(0)

  const problems = page.getByRole('list', { name: 'Out of order or not ready' })
  await expect(problems).toContainText(
    'This shows the reader “Juno Marsh is a member of The Ferry Company” plainly, before “Lamplight”, where it is meant to come out.',
  )
  await expect(page.getByRole('list', { name: 'Gaps to fill' })).toContainText('comes out here with no hint in an earlier scene')

  // What must be true before the wreck: one required, from a fact, turned around; one preferred, in words.
  const wreck = page.locator(`#plan-${ids.wreck}`)
  await wreck.getByText('Move, plan, remove').click()
  await wreck.getByRole('button', { name: 'Before and after' }).click()
  const conditions = page.getByRole('dialog', { name: 'Before and after “The Wreck”' })
  await conditions.getByRole('button', { name: 'Add something that must be true first' }).click()
  await conditions.getByLabel('Which entry (Before this scene, condition 1)').selectOption(ids.juno)
  await conditions.getByLabel('Start from a fact in your story bible').selectOption(ids.atLight)
  await conditions.getByLabel('Must or must not (Before this scene, condition 1)').selectOption('lacks')
  await conditions.getByRole('button', { name: 'Add something that must be true first' }).click()
  await conditions.getByLabel('How it is said (Before this scene, condition 2)').selectOption('words')
  await conditions.getByLabel('How much it matters (Before this scene, condition 2)').selectOption('soft')
  await conditions.getByLabel('What must be true, in your words').fill('The storm has broken')
  await expectAccessible(page)
  await shot(page, 'conditions-dialog', false)
  await conditions.getByRole('button', { name: 'Save' }).click()
  await expect(conditions).toHaveCount(0)

  const around = page.getByRole('list', { name: 'Before and after The Wreck' })
  await expect(around.getByRole('listitem').first()).toContainText(
    'Before · required Juno Marsh must not have this: it is located at The Lighthouse.',
  )
  await expect(around.getByRole('listitem').first()).toContainText('Not true yet')
  await expect(around.getByRole('listitem').nth(1)).toContainText('Before · preferred The storm has broken Not checked')
  await expect(problems).toContainText('This scene needs this first, and it is not true at that point')

  // The promises: the ferry is kept two scenes on; whether she leaves is still owed.
  // Other browser tests share this story, so the total owed may include their threads.
  await expect(page.getByRole('link', { name: /promises? to the reader still owed/ })).toBeVisible()
  const owed = page.getByRole('list', { name: 'Promises still owed' })
  await expect(owed).toContainText('Will Juno leave the island?')
  const kept = page.getByRole('list', { name: 'Promises kept' })
  await expect(kept).toContainText('Who sank the ferry?')
  await expect(kept).toContainText('Resolved in Lamplight')
  await expect(kept).toContainText('2 scenes later')
  await expectAccessible(page)
  await shot(page, 'plan')
  await page.getByRole('heading', { name: 'Promises to the reader' }).scrollIntoViewIfNeeded()
  await shot(page, 'promises', false)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/plan`)
  await expect(page.getByRole('list', { name: 'Out of order or not ready' })).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(390)
  await shot(page, 'phone')
  await page.getByRole('heading', { name: 'Promises to the reader' }).scrollIntoViewIfNeeded()
  await shot(page, 'phone-promises', false)

  // Leave the shared story as the other tests expect it.
  for (const planId of [ids.warning, ids.wreck, ids.lamplight, ids.sank, ids.leave]) {
    await call(page, 'POST', `/plan/${planId}/remove`, { expected_revision: await head(page) })
  }
})
