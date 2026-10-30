import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Richer context for the assistant (CTX-005, CTX-006, CTX-006A, CTX-007), in a real
// browser against a real backend with the local stand-in model (scripts/e2e/fake_model.py):
// - how much an entry matters, set on its page;
// - a summary of a scene suggested by the assistant, corrected and kept by the author;
// - a later scene's context with earlier scenes' summaries and events, the end of the
//   scene before as written, a passage from a scene on the same thread, and what moved
//   each item up or down.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, target?: ReturnType<Page['locator']>) {
  if (!SHOTS) return
  if (target) await target.screenshot({ path: `${SHOTS}/A-${name}.png` })
  else await page.screenshot({ path: `${SHOTS}/A-${name}.png` })
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

async function openContext(page: Page, title: string) {
  await page.goto(`/projects/${PROJECT}/plan`)
  const row = page.getByRole('listitem').filter({ hasText: title }).first()
  await row.getByText('Move, plan, remove').click()
  await row.getByRole('button', { name: 'What the assistant would use' }).click()
  return page.getByRole('dialog', { name: new RegExp(`What the assistant would use for “${title}”`) })
}

test('later scenes get earlier ones as summaries, events and prose, and say why each ranks where it does', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Hollis'],
    ['quay', 'Location', 'Salt Quay'],
    ['bram', 'Character', 'Bram Ottery'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.wren,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.quay, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  const thread = await call(page, 'POST', '/plan', {
    kind: 'thread',
    title: 'The missing ledger',
    expected_revision: await head(page),
  })
  const scene = async (title: string, summary: string, fields: Record<string, unknown> = {}) =>
    call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      summary,
      refs: [
        { entity_id: ids.wren, role: 'point_of_view' },
        { entity_id: ids.quay, role: 'place' },
        { entity_id: ids.bram, role: 'participant' },
      ],
      expected_revision: await head(page),
      ...fields,
    })
  const first = await scene('The ledger goes missing', 'Wren finds the counting house empty.', {
    sets_up: [thread.id],
  })
  const second = await scene('Fog on the quay', 'Wren waits for Bram in the fog.')
  await scene('The ledger found', 'Bram brings the ledger back.', { pays_off: [thread.id] })
  await call(page, 'PUT', `/plan/${first.id}/studio/text`, {
    content:
      'Wren Hollis pushed open the door of the counting house. The shelf where the ledger lived was bare.\n\n' +
      'Somewhere on Salt Quay a bell rang, and nobody came.',
    base_version_id: null,
  })
  await call(page, 'PUT', `/plan/${second.id}/studio/text`, {
    content:
      'The fog came down over Salt Quay before noon.\n\n' +
      'Wren Hollis counted the lamps and lost count, and Bram Ottery did not come.',
    base_version_id: null,
  })
  // An event the author placed in the first scene, through the ordinary reviewed path.
  const proposed = await call(page, 'POST', '/mutations', {
    origin: 'author',
    expected_revision: await head(page),
    operations: [
      {
        op: 'create_event',
        id: 'tmp:e1',
        kind: 'happening',
        name: 'The ledger is taken from the counting house',
        scene_id: first.id,
      },
    ],
  })
  if (proposed.requires_review) await call(page, 'POST', `/mutations/${proposed.id}/approve`, { note: 'ok' })
  await call(page, 'POST', `/mutations/${proposed.id}/commit`)
  // The worker reads the changed texts in; wait until nothing is waiting.
  await expect
    .poll(async () => (await call(page, 'GET', '/texts-index')).waiting, { timeout: 20_000 })
    .toBe(0)

  // How much Wren matters, set on her page.
  await page.goto(`/projects/${PROJECT}/entries/${ids.wren}`)
  const importance = page.getByLabel('How much it matters')
  await importance.selectOption('main')
  await expect(importance).toHaveValue('main')
  await expect(page.getByText(/The story is about them\. The assistant gives entries that matter more room/)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '1-entry-importance', page.locator('.entry-head'))

  // The assistant suggests a summary of the second scene; the author corrects and keeps it.
  let dialog = await openContext(page, 'Fog on the quay')
  const panel = dialog.getByRole('region', { name: 'Summary for later scenes' })
  await expect(panel.getByText(/No summary kept yet/)).toBeVisible()
  await panel.getByRole('button', { name: 'Ask the assistant to suggest one' }).click()
  await expect(panel.getByText('The assistant suggests')).toBeVisible()
  await expect(panel.getByText('Not used until you keep it.')).toBeVisible()
  await expectAccessible(page)
  await panel.scrollIntoViewIfNeeded()
  await shot(page, '2-summary-suggested')
  await panel.getByRole('button', { name: 'Correct it' }).click()
  const box = panel.getByLabel('Correct the suggestion, then keep it')
  await box.fill('Fog covers Salt Quay. Wren waits for Bram, who does not come.')
  await panel.getByRole('button', { name: 'Keep my correction' }).click()
  await expect(panel.getByText('Suggested by the assistant and corrected by you')).toBeVisible()
  await dialog.getByRole('button', { name: 'Done' }).click()

  // The third scene's context: earlier scenes, the scene before as written, the thread.
  dialog = await openContext(page, 'The ledger found')
  const earlier = dialog.getByRole('region', { name: 'What happened in earlier scenes' })
  await expect(
    earlier.getByText('“Fog on the quay”, as written: Fog covers Salt Quay. Wren waits for Bram, who does not come.'),
  ).toBeVisible()
  await expect(earlier.getByText('In “The ledger goes missing”: The ledger is taken from the counting house.')).toBeVisible()
  await expect(earlier.getByText(/It is two scenes back/).first()).toBeVisible()
  const before = dialog.getByRole('region', { name: 'How the scene before ends' })
  await expect(before.getByText(/and Bram Ottery did not come\./)).toBeVisible()
  // The thread's opening scene is two back on the same thread: its ending is given once, under
  // the nearby scenes, rather than again among the author's other writing.
  const nearby = dialog.getByRole('region', { name: 'How other recent scenes end' })
  await expect(nearby.getByText(/The shelf where the ledger lived was bare\./)).toBeVisible()
  await expect(dialog.getByText('Raised: Important to you').first()).toBeVisible()
  await expectAccessible(page)
  await earlier.scrollIntoViewIfNeeded()
  await shot(page, '3-context-layers')
  await before.scrollIntoViewIfNeeded()
  await shot(page, '3b-context-scene-before')

  // A phone: the same dialog at 390 wide, no sideways scrolling.
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(earlier).toBeVisible()
  await earlier.scrollIntoViewIfNeeded()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await shot(page, '4-context-layers-phone')

  // Dark.
  await page.setViewportSize({ width: 1280, height: 900 })
  await dialog.getByRole('button', { name: 'Done' }).click()
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  dialog = await openContext(page, 'The ledger found')
  const darkEarlier = dialog.getByRole('region', { name: 'What happened in earlier scenes' })
  await expect(darkEarlier).toBeVisible()
  await expectAccessible(page)
  await darkEarlier.scrollIntoViewIfNeeded()
  await shot(page, '5-context-dark')
})
