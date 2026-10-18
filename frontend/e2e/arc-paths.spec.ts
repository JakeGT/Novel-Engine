import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// A character's arc as a path of stages, in a real browser against a real backend, with
// no AI provider (STY-006): started by hand from the character's page, each stage placed
// in a scene of the plan, a stage reached before the one above it flagged, the arc shown
// as a lane on the plan, and the viewpoint character's change given to drafting.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/arcs-${name}.png`, fullPage })
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

test('a character arc is written stage by stage, checked, and shown on the plan', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Hollis'],
    ['tobin', 'Character', 'Tobin Crane'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  ids.knows = (
    await call(page, 'POST', '/facts', {
      proposition: {
        subject_id: ids.wren,
        predicate: 'KNOWS',
        object: { kind: 'entity_ref', entity_id: ids.tobin, project_id: PROJECT },
      },
      expected_revision: await head(page),
    })
  ).id
  const add = async (body: Record<string, unknown>) =>
    (await call(page, 'POST', '/plan', { ...body, expected_revision: await head(page) })).id as string
  const pov = [{ entity_id: ids.wren, role: 'point_of_view' }]
  ids.quay = await add({ kind: 'scene', title: 'The Quay', refs: pov })
  ids.squall = await add({ kind: 'scene', title: 'The Squall', refs: pov })
  ids.vigil = await add({ kind: 'scene', title: 'The Vigil', refs: pov })

  // Started by hand, from her own page.
  await page.goto(`/projects/${PROJECT}/entries/${ids.wren}`)
  const panel = page.getByRole('region', { name: 'How Wren Hollis changes' })
  await expect(panel).toContainText('No arc yet.')
  await panel.getByRole('button', { name: 'Start an arc for Wren Hollis' }).click()
  const dialog = page.getByRole('dialog', { name: 'Start an arc for Wren Hollis' })
  await dialog.getByLabel('Name of the arc').fill('Learning to trust')
  const stage = (n: number) => dialog.getByRole('group', { name: new RegExp(`^Stage ${n}`) })
  await stage(1).getByLabel('Where they stand').fill('Trusts no one')
  await stage(2).getByLabel('Where they stand').fill('Trusts Tobin')
  await stage(2).getByLabel('Reached in').selectOption(ids.vigil)
  await stage(2).getByLabel('True from then on (optional)').selectOption(ids.knows)
  await dialog.getByRole('button', { name: 'Add a stage' }).click()
  await stage(3).getByLabel('Where they stand').fill('Doubts him')
  await stage(3).getByLabel('Reached in').selectOption(ids.squall)
  await expectAccessible(page)
  await shot(page, 'dialog', false)
  await dialog.getByRole('button', { name: 'Start the arc' }).click()
  await expect(dialog).toHaveCount(0)

  // Doubting him in the squall comes before trusting him at the vigil: out of order.
  const lane = panel.getByRole('list', { name: 'Stages of “Learning to trust”, in order' })
  await expect(lane.getByRole('listitem')).toHaveCount(3)
  await expect(lane.getByRole('listitem').nth(1)).toContainText('The Vigil (scene 3)')
  await expect(lane.getByRole('listitem').nth(1)).toContainText('From then on: Wren Hollis knows Tobin Crane True by then')
  const notes = panel.getByRole('list', { name: 'Worth a look in Learning to trust' })
  await expect(notes).toContainText('“Doubts him” is reached in “The Squall”, which comes before “The Vigil”')
  await expectAccessible(page)
  await shot(page, 'entry')

  // Put it right: doubt comes first.
  await panel.getByRole('button', { name: 'Change the stages of Learning to trust' }).click()
  const editing = page.getByRole('dialog', { name: 'Stages of “Learning to trust”' })
  await editing.getByRole('button', { name: 'Move Stage 3 earlier' }).click()
  await editing.getByRole('button', { name: 'Save the stages' }).click()
  await expect(editing).toHaveCount(0)
  await expect(notes).toHaveCount(0)

  // On the plan: a lane under the arc, and the scene that moves it says so.
  const arcs = (await call(page, 'GET', '/plan')).arcs as { arc_id: string }[]
  ids.arc = arcs.find((reading) => reading.arc_id)?.arc_id ?? ''
  await panel.getByRole('link', { name: 'See it in the plan' }).click()
  const arcRow = page.locator(`#plan-${ids.arc}`)
  await expect(arcRow).toBeInViewport()
  await expect(arcRow.getByRole('list', { name: 'Stages of “Learning to trust”, in order' })).toContainText(
    'Doubts him',
  )
  await expect(page.locator(`#plan-${ids.vigil}`)).toContainText('Arc: Wren Hollis reaches “Trusts Tobin”')
  await expectAccessible(page)
  await arcRow.scrollIntoViewIfNeeded()
  await shot(page, 'plan', false)

  // Drafting the vigil is told where her arc stands going in and where it gets to.
  const context = await call(page, 'GET', `/plan/${ids.vigil}/context`)
  const given = (context.items as { key: string; text: string }[]).find((item) => item.key.startsWith('arc:'))
  expect(given?.text).toBe(
    'Wren Hollis\'s arc “Learning to trust”: going in, Wren Hollis is at “Doubts him”; in this scene, ' +
      'Wren Hollis reaches “Trusts Tobin”. From here on: Wren Hollis knows Tobin Crane.',
  )

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/entries/${ids.wren}`)
  await expect(page.getByRole('region', { name: 'How Wren Hollis changes' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.getByRole('region', { name: 'How Wren Hollis changes' }).scrollIntoViewIfNeeded()
  await shot(page, 'phone-entry', false)
  await page.goto(`/projects/${PROJECT}/plan`)
  await expect(page.locator(`#plan-${ids.arc}`)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.locator(`#plan-${ids.arc}`).scrollIntoViewIfNeeded()
  await shot(page, 'phone-plan', false)

  // Leave the shared story as the other tests expect it.
  for (const planId of [ids.arc, ids.quay, ids.squall, ids.vigil]) {
    await call(page, 'POST', `/plan/${planId}/remove`, { expected_revision: await head(page) })
  }
})
