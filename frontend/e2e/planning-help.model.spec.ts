import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Planning help in a real browser against a real backend, with the local stand-in model
// (scripts/e2e/fake_model.py), which answers the intent and bridge requests by their
// shape (STY-003, STY-004), and checks that need no model (STY-005, STY-006, SCN-007,
// CON-004):
// - scales the characters share, set up on the plan, with an arc's stages on one;
// - what a scene is for, suggested with its cost first, kept through Review;
// - a scene to bridge what the next one needs, suggested and kept through Review;
// - the studio noting prose that gives someone eyes their accepted details contradict.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, target?: ReturnType<Page['locator']>) {
  if (!SHOTS) return
  if (target) await target.screenshot({ path: `${SHOTS}/D-${name}.png` })
  else await page.screenshot({ path: `${SHOTS}/D-${name}.png`, fullPage: true })
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
const keep = async (page: Page, mutationId: string) => {
  await call(page, 'POST', `/mutations/${mutationId}/approve`, {})
  await call(page, 'POST', `/mutations/${mutationId}/commit`)
}

test('scales, a scene’s intent and a bridge scene are suggested for review, and prose is checked', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['mara', 'Character', 'Mara Quell'],
    ['kestle', 'Location', 'Kestle'],
    ['saltmere', 'Location', 'Saltmere'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.mara,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.kestle, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.mara,
      predicate: 'HAS_DETAIL',
      object: { kind: 'structured', value: { appearance: 'Grey eyes and short black hair.' } },
    },
    expected_revision: await head(page),
  })
  const quarrel = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'The quarrel',
    summary: 'Mara falls out with the harbour crew at Kestle.',
    refs: [{ entity_id: ids.mara, role: 'point_of_view' }],
    expected_revision: await head(page),
  })
  const room = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'The strongroom',
    summary: 'Mara tries to steal the ledger before the tide turns.',
    refs: [{ entity_id: ids.mara, role: 'point_of_view' }],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${room.id}/conditions`, {
    conditions: [
      {
        when: 'before',
        entity_id: ids.mara,
        check: { kind: 'has', pattern: { predicate: 'LOCATED_AT', other_id: ids.saltmere } },
      },
    ],
    expected_revision: await head(page),
  })
  const arc = await call(page, 'POST', '/plan', {
    kind: 'arc',
    title: 'Mara learns to trust',
    refs: [{ entity_id: ids.mara, role: 'about' }],
    expected_revision: await head(page),
  })

  // Scales: set up on the plan, by hand.
  await page.goto(`/projects/${PROJECT}/plan`)
  const scales = page.locator('section[aria-labelledby="scales-heading"]')
  await expect(scales.getByText(/No scales yet/)).toBeVisible()
  await scales.getByRole('button', { name: 'Add a scale' }).click()
  const editor = page.getByRole('dialog', { name: 'Scales your characters share' })
  await editor.getByLabel('Name').fill('Trust')
  await editor.getByLabel(/Levels, one per line/).fill('None\nWary\nSome\nComplete')
  await editor.getByLabel('A character moves one level at a time').check()
  await shot(page, '01-scales-dialog', editor)
  await expectAccessible(page)
  await editor.getByRole('button', { name: 'Save the scales' }).click()
  await expect(editor).toHaveCount(0)

  await call(page, 'PUT', `/plan/${arc.id}/path`, {
    stages: [
      { name: 'Alone', scale_id: 'trust', level: 'None' },
      { name: 'Warming', scene_id: quarrel.id, scale_id: 'trust', level: 'Wary' },
      { name: 'Trusts', scene_id: room.id, scale_id: 'trust', level: 'Complete' },
    ],
    expected_revision: await head(page),
  })
  await page.reload()
  const table = page.getByRole('table', { name: 'Where each character stands on Trust' })
  await expect(table).toContainText('“The quarrel”: Wary')
  await expect(table).toContainText('Closer to where the arc ends')
  await expect(page.locator(`#plan-${arc.id}`).getByText(/moves 2 levels at once, from “Wary” to “Complete”/)).toBeVisible()
  await shot(page, '02-scales', scales)

  // What the strongroom scene is for: the cost first, then a suggestion in Review.
  const roomRow = page.locator(`#plan-${room.id}`)
  await roomRow.getByRole('button', { name: 'Suggest what this scene is for' }).click()
  const offer = roomRow.getByRole('group', { name: 'Suggest what this scene is for' })
  await expect(offer).toBeVisible()
  await expect(offer.getByText(/About [\d,]+ tokens/)).toBeVisible()
  await shot(page, '03-intent-cost', roomRow)
  await expectAccessible(page)
  await offer.getByRole('button', { name: 'Ask the assistant' }).click()
  const intentLink = offer.getByRole('link', { name: 'See the suggestion in Review' })
  await expect(intentLink).toBeVisible({ timeout: 30_000 })
  const intentMutation = (await intentLink.getAttribute('href'))!.split('/').pop()!
  await intentLink.click()
  await expect(page.getByText('Opening the suggested changes…')).toHaveCount(0)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('row', { name: /What the scene is for/ })).toContainText('Wants: Mara tries to steal the ledger')
  await shot(page, '04-intent-review')
  await keep(page, intentMutation)

  await page.goto(`/projects/${PROJECT}/plan`)
  const card = page.getByLabel('What the viewpoint wants in The strongroom')
  await expect(card).toContainText('Mara tries to steal the ledger')
  await expect(card).toContainText('the tide turns')

  // A bridge to Saltmere, which the strongroom needs first.
  const unmet = page.locator(`#plan-${room.id} .plan-conditions li`).first()
  await expect(unmet).toContainText('Not true yet')
  await unmet.getByRole('button', { name: 'Suggest a scene to bridge this' }).click()
  const bridgeOffer = unmet.getByRole('group', { name: 'Suggest a scene to bridge this' })
  await expect(bridgeOffer.getByText(/one or two scenes to come just before it/)).toBeVisible()
  await shot(page, '05-bridge-cost', page.locator(`#plan-${room.id}`))
  await bridgeOffer.getByRole('button', { name: 'Ask the assistant' }).click()
  const bridgeLink = bridgeOffer.getByRole('link', { name: 'See the suggestion in Review' })
  await expect(bridgeLink).toBeVisible({ timeout: 30_000 })
  const bridgeMutation = (await bridgeLink.getAttribute('href'))!.split('/').pop()!
  await bridgeLink.click()
  await expect(page.getByRole('row', { name: /What must be true/ }).first()).toContainText(
    'After (required): Mara Quell must have this',
  )
  await shot(page, '05b-bridge-review')
  await expectAccessible(page)
  await keep(page, bridgeMutation)
  await page.goto(`/projects/${PROJECT}/plan`)
  await page.reload()
  const titles = page.locator('.plan-item.kind-scene > .plan-head .plan-title')
  // Other tests share this story: the bridge sits between this test's two scenes.
  await expect(titles.filter({ hasText: 'On the road' })).toHaveCount(1)
  const order = await titles.allTextContents()
  const at = (title: string) => order.indexOf(title)
  expect(at('The quarrel')).toBeGreaterThan(-1)
  expect(at('On the road')).toBe(at('The quarrel') + 1)
  expect(at('The strongroom')).toBe(at('On the road') + 1)
  await expect(page.locator(`#plan-${room.id} .plan-conditions li`).first()).toContainText('“On the road”')
  await shot(page, '06-bridged')
  await expectAccessible(page)

  // The prose, checked against Mara's accepted details: a note quoting both.
  await call(page, 'PUT', `/plan/${room.id}/studio/text`, {
    content: 'Mara Quell rubbed her blue eyes and looked at the strongroom door.',
    base_version_id: null,
  })
  await page.goto(`/projects/${PROJECT}/plan/${room.id}/write`)
  await expect(page.getByText(/the story bible says: “Grey eyes and short black hair\.”/).first()).toBeVisible()
  await shot(page, '07-appearance-note')

  // Phone width, and dark.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/plan`)
  await expect(table).toBeVisible()
  await shot(page, '08-plan-390')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.reload()
  await expect(table).toBeVisible()
  await shot(page, '09-plan-dark', page.locator(`#plan-${room.id}`))
  await expectAccessible(page)
})
