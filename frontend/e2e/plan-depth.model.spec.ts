import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Planning depth in a real browser against a real backend, with the local stand-in model
// (scripts/e2e/fake_model.py) used only to make one draft to keep:
// - a thread that opens, moves along and resolves, shown as steps in outline order;
// - what the viewpoint character wants in a scene, said by hand on the plan;
// - the context now compared with the one a kept draft was written from.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, target?: ReturnType<Page['locator']>) {
  if (!SHOTS) return
  if (target) await target.screenshot({ path: `${SHOTS}/plan-depth-${name}.png` })
  else await page.screenshot({ path: `${SHOTS}/plan-depth-${name}.png`, fullPage: true })
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

test('threads move along, scenes say what the viewpoint wants, and a kept draft can be compared', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['oriel', 'Character', 'Oriel Vance'],
    ['quay', 'Location', 'Salt Quay'],
    ['bram', 'Character', 'Bram Ottery'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const thread = await call(page, 'POST', '/plan', {
    kind: 'thread',
    title: 'Who cut the mooring lines?',
    expected_revision: await head(page),
  })
  const arc = await call(page, 'POST', '/plan', {
    kind: 'arc',
    title: 'Oriel finds the saboteur',
    refs: [{ entity_id: ids.oriel, role: 'about' }],
    expected_revision: await head(page),
  })
  const scene = (title: string, position: number, fields: Record<string, unknown>) =>
    call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      parent_id: arc.id,
      refs: [
        { entity_id: ids.oriel, role: 'point_of_view' },
        { entity_id: ids.quay, role: 'place' },
      ],
      expected_revision: position,
      ...fields,
    })
  const first = await scene('Boats adrift at dawn', await head(page), {
    summary: 'Oriel finds three boats loose in the harbour.',
    sets_up: [thread.id],
    intent: { goal: 'get the boats back before the tide turns', obstacle: 'the current and no one to help', outcome: 'yes_but' },
  })
  const middle = await scene('The harbourmaster’s ledger', await head(page), {
    summary: 'Oriel reads who paid for berths last night.',
  })
  await scene('Bram at the bollard', await head(page), {
    summary: 'Oriel catches Bram with a knife at the lines.',
    pays_off: [thread.id],
  })

  // The plan says the thread opens and resolves with nothing in between moving it.
  await page.goto(`/projects/${PROJECT}/plan`)
  const needs = page.locator('details.card').filter({ hasText: 'the plan still needs' })
  await needs.locator('summary').click()
  await expect(needs.getByText('This thread opens and resolves, but no scene in between moves it along.')).toBeVisible()
  await shot(page, '01-gap', needs)

  // Saying the middle scene moves it along, by hand.
  const row = page.locator(`#plan-${middle.id}`)
  await row.getByRole('button', { name: `Edit ${middle.title}` }).click()
  const form = page.getByRole('form', { name: `Change ${middle.title}` })
  await form.getByLabel(/^Threads it moves along/).selectOption(thread.id)
  await form.getByLabel(/^Wants/).fill('find a name in the ledger')
  await form.getByLabel(/^In the way/).fill('the harbourmaster is watching')
  await form.getByLabel(/^How it ends for them/).selectOption('no_and')
  await shot(page, '02-form', page.getByRole('dialog'))
  await expectAccessible(page)
  await form.getByRole('button', { name: 'Save changes' }).click()
  await expect(form).toHaveCount(0)

  const steps = page.getByRole('list', { name: 'How “Who cut the mooring lines?” runs through the plan' })
  await expect(steps.getByRole('listitem')).toHaveText([
    'Opens Boats adrift at dawn',
    'Moves along The harbourmaster’s ledger',
    'Resolves Bram at the bollard',
  ])
  await expect(page.getByText('This thread opens and resolves, but no scene in between moves it along.')).toHaveCount(0)
  await shot(page, '03-thread-steps', page.locator('section[aria-labelledby="threads-heading"]'))
  const card = page.getByLabel('What the viewpoint wants in The harbourmaster’s ledger')
  await expect(card).toContainText('No, and')
  await shot(page, '04-scene-card', page.locator(`#plan-${middle.id}`))
  await expectAccessible(page)

  // Draft the first scene with the stand-in model and keep the draft.
  const job = await call(page, 'POST', `/plan/${first.id}/studio/drafts`, { variants: 1, words: 200 })
  await expect
    .poll(async () => (await call(page, 'GET', `/jobs/${job.id}`)).status, { timeout: 30_000 })
    .toBe('succeeded')
  const studio = await call(page, 'GET', `/plan/${first.id}/studio`)
  await call(page, 'POST', `/drafts/${studio.drafts[0].id}/keep`, {})

  // Then the story changes: a new person in the scene, and a different outcome.
  await call(page, 'PATCH', `/plan/${first.id}`, {
    refs: [
      { entity_id: ids.oriel, role: 'point_of_view' },
      { entity_id: ids.quay, role: 'place' },
      { entity_id: ids.bram, role: 'participant' },
    ],
    intent: { goal: 'get the boats back before the tide turns', obstacle: 'Bram will not lend his skiff', outcome: 'no_and' },
    expected_revision: await head(page),
  })

  await page.reload()
  const firstRow = page.locator(`#plan-${first.id}`)
  await firstRow.getByText('Move, plan, remove').click()
  await firstRow.getByRole('button', { name: 'What the assistant would use' }).click()
  const dialog = page.getByRole('dialog', { name: /What the assistant would use for “Boats adrift at dawn”/ })
  const choose = dialog.getByLabel('Compare with what a kept draft was written from')
  await expect(choose).toHaveValue('')
  await choose.selectOption({ index: 1 })
  const compare = dialog.getByRole('region', { name: 'Compare with a kept draft' })
  await expect(compare.getByText(/Since version 1 was drafted/)).toBeVisible()
  await expect(compare.getByText('Bram Ottery (character).')).toBeVisible()
  await expect(compare.getByText(/Before: .*the current and no one to help/)).toBeVisible()
  await shot(page, '05-compare', compare)
  await choose.scrollIntoViewIfNeeded()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/plan-depth-06-dialog.png` })
  await expectAccessible(page)
})
