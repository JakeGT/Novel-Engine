import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Batch 10, area D, in a real browser against a real backend with no AI provider: travel
// read from a scene's words (SCN-007), how the plan's conditions and the reader's guide
// would read after a change, worked out on a copy and shown as a picture too (CON-017),
// and asking the assistant about only a what-if's changed scenes, up to its cost (CON-011).
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/d-${name}.png`, fullPage })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(
    results.violations.map(
      (violation) => `${violation.id}: ${violation.help} [${violation.nodes.map((node) => node.target.join(' ')).join(' | ')}]`,
    ),
  ).toEqual([])
}

async function call(page: Page, method: string, path: string, data?: unknown, headers: Record<string, string> = {}) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json', ...headers },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page, headers: Record<string, string> = {}) =>
  (await call(page, 'GET', '/projection', undefined, headers)).head_revision as number

test('travel in the words, a change read on a copy, and the changed scenes of a what-if', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['brisa', 'Character', 'Brisa Tallow'],
    ['mill', 'Location', 'Corran Mill'],
    ['ford', 'Location', 'Vessick Ford'],
    ['weir', 'Faction', 'The Weir Guild'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  ids.inWeir = (
    await call(page, 'POST', '/facts', {
      proposition: {
        subject_id: ids.brisa,
        predicate: 'MEMBER_OF',
        object: { kind: 'entity_ref', entity_id: ids.weir, project_id: PROJECT },
      },
      expected_revision: await head(page),
    })
  ).id
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Dusk at Corran Mill',
    refs: [
      { entity_id: ids.brisa, role: 'point_of_view' },
      { entity_id: ids.mill, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${scene.id}/reveals`, {
    reveals: [{ fact_id: ids.inWeir, how: 'shown' }],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${scene.id}/conditions`, {
    conditions: [
      {
        when: 'before',
        strength: 'hard',
        entity_id: ids.brisa,
        check: { kind: 'has', pattern: { predicate: 'MEMBER_OF', other_id: ids.weir } },
      },
    ],
    expected_revision: await head(page),
  })

  // The words take Brisa away from the plan's place: a note in the studio, quoting them.
  const words =
    'Brisa Tallow waited by the wheel while the water ran black. Brisa Tallow rode to Vessick Ford before dark.\n\n' +
    '“You will never ride to Vessick Ford,” the miller said.'
  await call(page, 'PUT', `/plan/${scene.id}/studio/text`, { content: words, base_version_id: null })
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  await expect(page.getByText(/the plan places this scene at Corran Mill/).first()).toBeVisible()
  await expectAccessible(page)
  await shot(page, '1-studio-travel-1280', false)

  // A suggested change: how the scene's condition and the reader's guide would read after it.
  const proposed = await call(page, 'POST', '/mutations', {
    origin: 'model',
    expected_revision: await head(page),
    operations: [{ op: 'close_fact', id: ids.inWeir }],
  })
  const before = await head(page)
  await page.goto(`/projects/${PROJECT}/review/${proposed.id}`)
  const touches = page.getByRole('region', { name: 'What this change touches' })
  const after = touches.getByRole('region', { name: 'After the change' })
  await expect(after).toContainText('on a copy of your story')
  await expect(after.getByRole('heading', { name: 'Scene conditions' })).toBeVisible()
  await expect(after).toContainText('would no longer be met')
  await expect(after).toContainText('The reader would no longer learn: Brisa Tallow is a member of The Weir Guild')
  await touches.getByRole('button', { name: 'Show it as a picture' }).click()
  const picture = touches.getByRole('img', { name: /What this change touches, as a picture/ })
  await expect(picture).toBeVisible()
  await expect(touches.getByRole('button', { name: 'Hide the picture' })).toHaveAttribute('aria-expanded', 'true')
  await expectAccessible(page)
  await touches.scrollIntoViewIfNeeded()
  await shot(page, '2-impact-after-1280', false)
  // Nothing was saved by reading it.
  expect(await head(page)).toBe(before)
  // At phone width and in the dark theme, as someone reviewing on a phone at night would see it.
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.reload()
  await touches.getByRole('button', { name: 'Show it as a picture' }).click()
  await picture.scrollIntoViewIfNeeded()
  await expect(picture).toBeVisible()
  await expectAccessible(page)
  await shot(page, '3-impact-after-390-dark', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.setViewportSize({ width: 1280, height: 900 })

  // A what-if with the scene written differently: the assistant is offered only that scene,
  // with the cost first. No provider is set up here, so it stops before anything is sent.
  const whatIf = await call(page, 'POST', '/what-ifs', { name: 'Brisa stays at the mill' })
  const inWhatIf = { 'X-Story-Branch': whatIf.id }
  const studio = await call(page, 'GET', `/plan/${scene.id}/studio`, undefined, inWhatIf)
  await call(
    page,
    'PUT',
    `/plan/${scene.id}/studio/text`,
    { content: 'Brisa Tallow stayed by the wheel all night and counted the sacks.', base_version_id: studio.version_id },
    inWhatIf,
  )
  await page.goto(`/projects/${PROJECT}/what-ifs/${whatIf.id}/audit`)
  const ask = page.getByRole('region', { name: 'Ask the assistant about the changed scenes' })
  await expect(ask).toContainText('Only the scene written differently in this what-if')
  await expect(ask.getByText(/About [\d,]+ tokens in all/)).toBeVisible()
  await expect(ask.getByRole('button', { name: 'Check the changed scene' })).toBeEnabled()
  await expectAccessible(page)
  await shot(page, '4-what-if-changed-1280')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await ask.scrollIntoViewIfNeeded()
  await expect(ask.getByText(/About [\d,]+ tokens in all/)).toBeVisible()
  await shot(page, '5-what-if-changed-390', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)

  // The story's home page has no notice to show: none of the author's audits ran on a schedule.
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/projects/${PROJECT}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Audits you scheduled' })).toHaveCount(0)
})

// A scheduled audit only comes due after days, so the notice it leaves is shown here with
// the notices answer stood in for (its server side is covered by the contract tests): how
// the story's home page tells the author what it found and used, until marked read.
test('the home page tells the author what a scheduled audit found and used', async ({ page }) => {
  let seen = false
  await page.route('**/api/v1/projects/*/notices**', async (route) => {
    if (route.request().method() === 'POST') {
      seen = true
      return route.fulfill({ status: 204 })
    }
    return route.fulfill({
      json: {
        items: seen
          ? []
          : [
              {
                id: 'ntc_shown',
                kind: 'audit_finished',
                job_id: 'job_shown',
                status: 'succeeded',
                sentence:
                  'Your scheduled audit of “Act One” finished. It read 5 scenes and found 3 notes from the assistant and 2 findings from the checks without it. It used 12,400 tokens (about $0.02).',
                part: '“Act One”',
                plan_id: 'pln_act',
                what_if: null,
                branch_id: null,
                scenes: 5,
                notes: 3,
                checks: 2,
                total_tokens: 12400,
                cost_usd: 0.02,
                created_at: new Date().toISOString(),
                seen: false,
              },
            ],
      },
    })
  })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  const notices = page.getByRole('region', { name: 'Audits you scheduled' })
  await expect(notices).toContainText('found 3 notes from the assistant')
  await expect(notices.getByRole('link', { name: 'Open the audit' })).toBeVisible()
  await expectAccessible(page)
  await notices.scrollIntoViewIfNeeded()
  await shot(page, '6-home-notice-1280', false)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await notices.scrollIntoViewIfNeeded()
  await shot(page, '7-home-notice-390', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await notices.getByRole('button', { name: 'Mark as read' }).click()
  await expect(notices).toHaveCount(0)
})
