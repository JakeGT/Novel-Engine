import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// What a change touches, shown before it is approved (CON-017), in a real browser against a
// real backend with no AI provider: the review screen lists the scenes, threads and writing
// that depend on a suggested change, says what it did not check, and links to each; the entry
// page's edit offers the same for the entry.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/impact-${name}.png`, fullPage })
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

test('a suggested change shows what depends on it before it is added', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['odile', 'Character', 'Odile Brand'],
    ['rock', 'Location', 'Gull Rock'],
    ['guild', 'Faction', 'The Salt Guild'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  ids.inGuild = (
    await call(page, 'POST', '/facts', {
      proposition: {
        subject_id: ids.odile,
        predicate: 'MEMBER_OF',
        object: { kind: 'entity_ref', entity_id: ids.guild, project_id: PROJECT },
      },
      expected_revision: await head(page),
    })
  ).id
  const add = async (body: Record<string, unknown>) =>
    (await call(page, 'POST', '/plan', { ...body, expected_revision: await head(page) })).id as string
  ids.thread = await add({
    kind: 'thread',
    title: 'Will Odile betray the guild?',
    refs: [{ entity_id: ids.odile, role: 'about' }],
  })
  ids.scene = await add({
    kind: 'scene',
    title: 'Low Tide at Gull Rock',
    refs: [
      { entity_id: ids.odile, role: 'point_of_view' },
      { entity_id: ids.rock, role: 'place' },
    ],
  })
  await call(page, 'PUT', `/plan/${ids.scene}/reveals`, {
    reveals: [{ fact_id: ids.inGuild, how: 'hinted' }],
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/texts', {
    title: 'Odile, a first sketch',
    content: 'Odile Brand kept the guild’s ledger under her bed.\n\nThe tide came in.',
  })
  // A saved text is read into the index of the author's writing by the background worker
  // (CTX-005); wait until it is in before asking what a change touches.
  await expect
    .poll(async () => (await call(page, 'GET', '/texts-index')).waiting as number, { timeout: 15000 })
    .toBe(0)
  const proposed = await call(page, 'POST', '/mutations', {
    origin: 'model',
    expected_revision: await head(page),
    operations: [{ op: 'close_fact', id: ids.inGuild }],
  })

  await page.goto(`/projects/${PROJECT}/review/${proposed.id}`)
  const touches = page.getByRole('region', { name: 'What this change touches' })
  await expect(touches).toBeVisible()
  const affected = touches.getByRole('region', { name: 'Affected', exact: true })
  await expect(affected.getByRole('link', { name: 'Low Tide at Gull Rock' })).toBeVisible()
  await expect(affected).toContainText('Odile Brand is the point of view')
  await expect(affected).toContainText('It hints to the reader: Odile Brand is a member of The Salt Guild')
  await expect(affected.getByRole('link', { name: 'Will Odile betray the guild?' })).toBeVisible()
  const maybe = touches.getByRole('region', { name: 'May be affected' })
  await expect(maybe.getByRole('link', { name: 'Odile, a first sketch' })).toBeVisible()
  await expect(touches.getByRole('heading', { name: 'Not checked' })).toBeVisible()
  await expect(touches).toContainText('without a name or alias the story bible knows')
  await expectAccessible(page)
  await touches.scrollIntoViewIfNeeded()
  await shot(page, 'review', false)
  // At phone width, from a fresh load, as someone on a phone would arrive.
  await page.setViewportSize({ width: 390, height: 900 })
  await page.reload()
  await touches.scrollIntoViewIfNeeded()
  await shot(page, 'review-390', false)
  await shot(page, 'review-390-full')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.reload()

  // Skipping the only change: nothing is touched any more.
  await page.getByRole('group', { name: /Change 1:/ }).getByRole('button', { name: 'Skip' }).click()
  await expect(touches).toContainText('Every change is skipped, so nothing in your story is touched.')
  await page.getByRole('group', { name: /Change 1:/ }).getByRole('button', { name: 'Keep' }).click()
  await expect(affected.getByRole('link', { name: 'Low Tide at Gull Rock' })).toBeVisible()

  // Following a scene opens it in the plan.
  await affected.getByRole('link', { name: 'Low Tide at Gull Rock' }).click()
  await expect(page).toHaveURL(new RegExp(`/plan#plan-${ids.scene}$`))
  await expect(page.locator(`#plan-${ids.scene}`)).toBeVisible()

  // The entry's own edit says what depends on it before saving.
  await page.goto(`/projects/${PROJECT}/entries/${ids.odile}`)
  await page.getByRole('button', { name: 'Edit entry' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit Odile Brand' })
  const summary = dialog.getByText(/What changing Odile Brand touches/)
  await expect(summary).toContainText('affected')
  await summary.click()
  await expect(dialog).toContainText('Low Tide at Gull Rock')
  await expect(dialog).toContainText('Will Odile betray the guild?')
  await expectAccessible(page)
  await shot(page, 'entry-edit', false)
})
