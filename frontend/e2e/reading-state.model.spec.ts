import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Reading a scene finds changes of state and a new thread (EXT-001), in a real browser
// against a real backend with the local stand-in model (scripts/e2e/fake_model.py), which
// answers "<someone> came to <a place> at dusk." with a location that begins, and "Nobody
// could say who had taken …" with a new thread:
// - Ida, recorded at Brack Hollow, comes to Fennick Weir in a scene set at year 12: the
//   new place starts then and the old one ends then, each quoting the scene;
// - the missing sack becomes a new thread the scene opens, in one change to the plan;
// - nothing enters the story until it is reviewed.
// The whole story is shared with the other browser tests, so everything here has its own
// names. Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.screenshot({ path: `${SHOTS}/D-${name}.png` })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
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

const PROSE =
  'The rain had stopped by evening. Ida Marsh came to Fennick Weir at dusk. ' +
  'Nobody could say who had taken the last sack.'

test('a scene read into the story suggests a change of place and a new thread, for review', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ida', 'Character', 'Ida Marsh'],
    ['weir', 'Location', 'Fennick Weir'],
    ['hollow', 'Location', 'Brack Hollow'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.ida,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.hollow, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Dusk at the weir',
    summary: 'Ida reaches the weir and finds a sack gone.',
    refs: [
      { entity_id: ids.ida, role: 'point_of_view' },
      { entity_id: ids.weir, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${scene.id}/when`, { when: { kind: 'at', at: 12 }, expected_revision: await head(page) })
  await call(page, 'PUT', `/plan/${scene.id}/studio/text`, { content: PROSE })

  // Reading the scene: what it finds waits for review.
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  await expect(page.getByRole('heading', { level: 1, name: 'Dusk at the weir' })).toBeVisible()
  const bible = page.getByRole('region', { name: 'Your story bible' })
  await bible.getByRole('button', { name: 'Update the story from this scene' }).click()
  await expect(page.getByText(/changes suggested from this scene\./)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/Nothing changes in your story until you review them\./)).toBeVisible()
  await shot(page, 'state-1-read')
  await expectAccessible(page)

  // Nothing is in the story yet: Ida is still only at Brack Hollow.
  const before = await call(page, 'GET', `/facts?subject_id=${ids.ida}&predicate=LOCATED_AT`)
  expect(before.items.map((fact: { object: { entity_id: string } }) => fact.object.entity_id)).toEqual([ids.hollow])

  // The change of place: the new place from year 12, the old one ending then, said why.
  const reading = (await call(page, 'GET', `/plan/${scene.id}/studio`)) as { jobs?: unknown }
  expect(reading).toBeTruthy()
  await page.getByRole('link', { name: 'Review them' }).click()
  await expect(page).toHaveURL(/\/review$/)
  await shot(page, 'state-2-queue')
  const pending = await call(page, 'GET', '/mutations?status=needs_review&limit=50')
  const mine: { id: string }[] = []
  for (const item of pending.items as { id: string }[]) {
    const detail = await call(page, 'GET', `/mutations/${item.id}`)
    const text = JSON.stringify(detail.operations)
    if (text.includes(ids.ida) || text.includes('The missing sack')) mine.push(item)
  }
  expect(mine.length).toBe(2)

  for (const [index, item] of mine.entries()) {
    await page.goto(`/projects/${PROJECT}/review/${item.id}`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await shot(page, `state-3-review-${index + 1}`)
    await expectAccessible(page)
  }
  const place = await call(page, 'GET', `/mutations/${mine[0].id}`)
  const thread = await call(page, 'GET', `/mutations/${mine[1].id}`)
  const ops = [...place.operations, ...thread.operations].map((op: { payload: Record<string, unknown> }) => op.payload)
  const begins = ops.find((op) => op.op === 'assert_fact') as Record<string, unknown>
  expect(begins.why).toBe('The passage shows this beginning.')
  expect((begins.starts as { at: number }).at).toBe(12)
  const ends = ops.find((op) => op.op === 'amend_fact') as Record<string, unknown>
  expect((ends.ends as { at: number }).at).toBe(12)
  const opened = ops.find((op) => op.op === 'create_plan_item') as Record<string, unknown>
  expect(opened.title).toBe('The missing sack')

  // On a phone, the review reads the same.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/review/${mine[0].id}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await shot(page, 'state-4-review-phone')
  await expectAccessible(page)
  await page.goto(`/projects/${PROJECT}/review/${mine[1].id}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await shot(page, 'state-5-thread-phone')

  // And in the dark theme.
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/projects/${PROJECT}/review/${mine[0].id}`)
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await expect(page.getByRole('button', { name: 'Switch to light' })).toBeVisible()
  await shot(page, 'state-6-review-dark')
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Switch to light' }).click()
})
