import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Continuity across story time and place (CON-003, CON-004) in a real browser against a
// real backend, with no AI provider. Works in the shared local story beside other tests'
// records, so everything it makes has its own names.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.CLAUDE_JOB_DIR ? `${process.env.CLAUDE_JOB_DIR}/tmp/batch5` : null

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

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/areaA-${name}.png`, fullPage: true })
}

test('continuity: two places at once, a journey too quick, and after a death', async ({ page }) => {
  const tag = Date.now().toString(36).slice(-4)
  const oriel = `Oriel ${tag}`
  const brack = `Brackwater ${tag}`
  const fennick = `Fennick ${tag}`
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['oriel', 'Character', oriel],
    ['brack', 'Location', brack],
    ['fennick', 'Location', fennick],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const scenes: Record<string, string> = {}
  for (const [key, title, place, at] of [
    // Fractions are exact moments; a whole year means some time in that year (CON-003).
    ['dock', `The dock ${tag}`, 'brack', 3.25],
    ['road', `The road ${tag}`, 'fennick', 3.25],
    ['return', `Home again ${tag}`, 'brack', 4.25],
  ] as const) {
    const made = await call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      refs: [
        { entity_id: ids[place], role: 'place' },
        { entity_id: ids.oriel, role: 'point_of_view' },
      ],
      expected_revision: await head(page),
    })
    scenes[key] = made.id
    await call(page, 'PUT', `/plan/${made.id}/when`, { when: { kind: 'at', at }, expected_revision: await head(page) })
  }

  // The timeline says what cannot all be true, with links to each record.
  await page.goto(`/projects/${PROJECT}/timeline`)
  await expect(page.getByRole('heading', { level: 1, name: 'Timeline' })).toBeVisible()
  const findings = page.getByRole('list', { name: 'Continuity findings' })
  const twice = findings.getByRole('listitem').filter({ hasText: `${oriel} is at` }).filter({ hasText: 'at the same time' })
  await expect(twice).toContainText('Cannot all be true')
  await expect(twice.getByRole('link', { name: `The dock ${tag}` })).toBeVisible()

  // A death (an event of the kind the checks read); the later scene is flagged.
  await call(page, 'POST', '/events', {
    kind: 'death',
    name: `${oriel} drowns`,
    participants: [{ entity_id: ids.oriel, role: 'participant' }],
    when: { kind: 'at', at: 3.5 },
    expected_revision: await head(page),
  })
  await page.reload()
  await expect(
    findings.getByRole('listitem').filter({ hasText: `“Home again ${tag}” (year 4.25), after their death` }),
  ).toBeVisible()

  // The author's own journey time: nothing is checked until it is set.
  await expect(findings.getByText(/getting from one to the other takes/)).toHaveCount(0)
  const table = page.getByRole('form', { name: 'How long things take' })
  await table.getByRole('button', { name: 'Add a journey' }).click()
  const journey = table.getByRole('group', { name: /^Journey \d+$/ }).last()
  await journey.getByLabel('From').selectOption({ label: brack })
  await journey.getByLabel('To').selectOption({ label: fennick })
  await journey.getByLabel('takes').fill('2')
  await expectAccessible(page)
  await table.getByRole('button', { name: 'Save how long things take' }).click()
  await expect(table.getByText('Saved. The checks above now use it.')).toBeVisible()
  await expect(
    findings.getByRole('listitem').filter({ hasText: `then at ${brack}` }).filter({ hasText: 'takes 2 years' }),
  ).toBeVisible()
  await page.getByText(/What these checks look at/).click()
  await expect(page.getByText('Not enough time to get there')).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'timeline-1280')
  await page.setViewportSize({ width: 390, height: 900 })
  await shot(page, 'timeline-390')
  await page.setViewportSize({ width: 1280, height: 900 })

  // The plan lists them with what is out of order, beside each scene.
  await page.goto(`/projects/${PROJECT}/plan`)
  const outOfOrder = page.getByRole('list', { name: 'Out of order or not ready' })
  const row = outOfOrder.getByRole('listitem').filter({ hasText: `The dock ${tag}` }).filter({ hasText: 'at the same time' })
  await expect(row.first()).toBeVisible()
  await expect(row.first().getByRole('link', { name: oriel })).toBeVisible()
  await expectAccessible(page)
  await outOfOrder.scrollIntoViewIfNeeded()
  if (SHOTS) await outOfOrder.screenshot({ path: `${SHOTS}/areaA-plan.png` })

  // The studio's checks for the scene include them, before any prose is written.
  await page.goto(`/projects/${PROJECT}/plan/${scenes.return}/write`)
  await expect(page.getByText(/after their death/).first()).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'studio')
})
