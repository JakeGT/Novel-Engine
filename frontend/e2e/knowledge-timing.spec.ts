import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// How a character came to know something, and checks kept as runs, in a real browser
// against a real backend, with no AI provider (KNW-002, CON-006, CON-001): a telling placed
// in a scene says so on the hearer's page; a character works something out from what they
// know; a scene earlier in the plan whose words use it is warned about, never refused; and
// each save keeps a record of the checks, marked out of date when the words change.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/C-${name}.png`, fullPage })
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

test('what a character learns is placed, worked out, warned about early, and each check is kept', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Tallow'],
    ['hale', 'Character', 'Hale Marsh'],
    ['lamps', 'Faction', 'The Lamplighters'],
    ['hall', 'Location', 'Wick Street Hall'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  for (const [predicate, target] of [
    ['MEMBER_OF', 'lamps'],
    ['LOCATED_AT', 'hall'],
  ]) {
    await call(page, 'POST', '/facts', {
      proposition: {
        subject_id: ids.hale,
        predicate,
        object: { kind: 'entity_ref', entity_id: ids[target], project_id: PROJECT },
      },
      expected_revision: await head(page),
    })
  }
  const early = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Before the Lamps Are Lit',
    summary: 'Wren watches Hale cross the square.',
    refs: [
      { entity_id: ids.wren, role: 'point_of_view' },
      { entity_id: ids.hale, role: 'participant' },
    ],
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'At Wick Street Hall',
    summary: 'Hale tells Wren who he answers to.',
    refs: [{ entity_id: ids.wren, role: 'point_of_view' }],
    expected_revision: await head(page),
  })

  // Hale tells Wren, in the later scene, that he is a Lamplighter.
  await page.goto(`/projects/${PROJECT}/entries/${ids.hale}`)
  await page.getByRole('region', { name: 'What Hale Marsh knows' }).getByRole('button', { name: 'Tell someone something' }).click()
  const tell = page.getByRole('dialog', { name: 'Tell someone something' })
  await tell.getByRole('group', { name: 'Who is told' }).getByLabel('Wren Tallow').check()
  await tell.getByRole('combobox', { name: 'Which fact' }).selectOption({ label: 'Hale Marsh is a member of The Lamplighters' })
  await tell.getByRole('combobox', { name: /In which scene/ }).selectOption({ label: 'At Wick Street Hall' })
  await shot(page, '01-tell-in-a-scene', false)
  await expectAccessible(page)
  await tell.getByRole('button', { name: 'Suggest what they believe' }).click()
  await page.getByRole('dialog', { name: 'Suggested for review' }).getByRole('link', { name: 'Review the suggestion' }).click()
  await page.getByRole('button', { name: /Approve and add to your story/ }).click()
  await expect(page.getByRole('dialog', { name: 'Added to your story' })).toBeVisible()

  // Wren's page says how and where she learned it; she works out where Hale will be.
  await page.goto(`/projects/${PROJECT}/entries/${ids.wren}`)
  const knows = page.getByRole('region', { name: 'What Wren Tallow knows' })
  await expect(knows).toContainText('Told by Hale Marsh · in “At Wick Street Hall”')
  await knows.getByRole('button', { name: 'Wren Tallow works something out' }).click()
  const infer = page.getByRole('dialog', { name: 'Wren Tallow works something out' })
  await infer.getByRole('combobox', { name: 'Which fact' }).selectOption({ label: 'Hale Marsh is located at Wick Street Hall' })
  await infer.getByRole('group', { name: 'From what' }).getByLabel('Hale Marsh is a member of The Lamplighters').check()
  await infer.getByRole('combobox', { name: /In which scene/ }).selectOption({ label: 'At Wick Street Hall' })
  await shot(page, '02-works-something-out', false)
  await expectAccessible(page)
  await infer.getByRole('button', { name: 'Save what they worked out' }).click()
  await page.getByRole('dialog', { name: 'Saved' }).getByRole('button', { name: 'Done' }).click()
  await expect(knows).toContainText('Worked it out · in “At Wick Street Hall”')
  await expect(knows).toContainText('From: Hale Marsh is a member of The Lamplighters.')
  await shot(page, '03-how-each-was-learned')
  await expectAccessible(page)

  // In the earlier scene, words that use it are a warning; the save still happens.
  await page.goto(`/projects/${PROJECT}/plan/${early.id}/write`)
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill('Wren Tallow watched the square.\n\nShe knew Hale Marsh had sworn to The Lamplighters.')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()
  const checks = page.getByRole('region', { name: 'Checks', exact: true })
  await expect(checks).toContainText('This may use something too early. Wren Tallow only learns this in “At Wick Street Hall”')
  const history = page.getByRole('region', { name: 'Check history' })
  await expect(history).toContainText('Checks without the assistant')
  await expect(history).toContainText('Version 1')
  await expect(history).toContainText('Up to date')
  await shot(page, '04-studio-warning-and-history')
  await expectAccessible(page)

  // Changing the words keeps a second record and marks the first out of date.
  await editor.fill('Wren Tallow watched the square, and Hale Marsh crossed it without a word.')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 2/)).toBeVisible()
  await expect(history.locator('.check-run')).toHaveCount(2)
  const older = history.locator('.check-run').nth(1)
  await expect(older).toContainText('Out of date')
  await expect(older).toContainText('The words have changed since.')
  await older.getByText(/^Show the/).click()
  await expect(older).toContainText('Fixed: the words changed')
  await shot(page, '05-history-out-of-date')
  await expectAccessible(page)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await history.scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await shot(page, '06-history-phone', false)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})
