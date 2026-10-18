import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Checks one pass at a time and deep audits (CON-008, CON-011) in a real browser against a
// real backend, with the local stand-in model (scripts/e2e/fake_model.py), which answers
// each pass by what it is asked to look for: the continuity pass and the clarity pass
// each note one passage, the others find nothing.
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

// The stand-in quotes these words; each scene holds some of them.
const FIRST =
  'Ada Penrose climbed the stair of The Lighthouse at dawn. The lamps along the quay were already lit. ' +
  'She counted the sacks twice and said nothing.'
const SECOND = 'Below, the boats came in. She counted the sacks twice before the storm broke.'

test('choose what a check looks at, then audit a part of the story', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ada', 'Character', 'Ada Penrose'],
    ['light', 'Location', 'The Lighthouse'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const beat = await call(page, 'POST', '/plan', {
    kind: 'beat',
    title: 'The storm comes in',
    expected_revision: await head(page),
  })
  const scenes: { id: string }[] = []
  for (const [title, text] of [
    ['Dawn at the light', FIRST],
    ['The boats come in', SECOND],
  ]) {
    const scene = await call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      parent_id: beat.id,
      refs: [
        { entity_id: ids.ada, role: 'point_of_view' },
        { entity_id: ids.light, role: 'place' },
      ],
      expected_revision: await head(page),
    })
    await call(page, 'PUT', `/plan/${scene.id}/studio/text`, { content: text, base_version_id: null })
    scenes.push(scene)
  }

  // In the studio: every check is offered, each with its cost, and the author chooses.
  await page.goto(`/projects/${PROJECT}/plan/${scenes[0].id}/write`)
  const choices = page.getByRole('group', { name: 'What the assistant checks' })
  await expect(choices.getByText(/about [\d,]+ tokens/).first()).toBeVisible()
  await expect(page.getByText(/About [\d,]+ tokens for all five\./)).toBeVisible()
  await choices.getByRole('checkbox', { name: /Your voice/ }).uncheck()
  await expect(page.getByText(/About [\d,]+ tokens for the 4 chosen\./)).toBeVisible()
  await shot(page, '01-studio-choices')
  await expectAccessible(page)
  await page.getByRole('button', { name: /Check with the assistant/ }).click()
  await expect(page.getByRole('region', { name: /The assistant.s notes/ }).getByText('The scene is set in the morning, but the lamps are lit.')).toBeVisible({ timeout: 30_000 })
  await expect(
    page.getByRole('region', { name: /The assistant.s notes/ }).getByText(/It is not clear whether she counts before or after/),
  ).toBeVisible()
  await expect(page.getByText('This note cannot be pinned to the text.')).toHaveCount(0)
  await shot(page, '02-studio-checked')

  // From the plan: audit the beat. The cost is said before anything is sent.
  await page.goto(`/projects/${PROJECT}/plan`)
  await page.getByLabel('Move, plan or remove The storm comes in').click()
  await page.getByRole('button', { name: 'Audit this part of the story: The storm comes in' }).click()
  const dialog = page.getByRole('dialog', { name: 'Audit “The storm comes in”' })
  await expect(dialog.getByText('2 written scenes')).toBeVisible()
  await expect(dialog.getByText(/About [\d,]+ tokens in all/)).toBeVisible()
  await dialog.getByRole('checkbox', { name: /Viewpoint and secrets/ }).uncheck()
  await shot(page, '03-audit-dialog', false)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Start the audit' }).click()

  // The results, scene by scene, each passage linked to the studio.
  await expect(page.getByRole('heading', { level: 1, name: 'Audit of “The storm comes in”' })).toBeVisible()
  await expect(page.getByText(/notes? from the assistant and/)).toBeVisible({ timeout: 60_000 })
  const first = page.getByRole('listitem', { name: 'Dawn at the light' })
  await expect(first.getByRole('region', { name: 'Continuity with the story bible' })).toContainText('lamps are lit')
  await expect(first.getByRole('region', { name: 'Clarity' })).toBeVisible()
  const second = page.getByRole('listitem', { name: 'The boats come in' })
  await expect(second.getByText('Checked')).toBeVisible()
  await expect(second.getByRole('region', { name: 'Clarity' })).toContainText('She counted the sacks twice')
  await shot(page, '04-audit-results')
  const auditUrl = page.url()
  await expectAccessible(page)

  await first.getByRole('region', { name: 'Continuity with the story bible' }).getByRole('link', { name: 'Show in the studio' }).click()
  await expect(page.getByText('The words the change came from are selected in your text.')).toBeVisible()
  const selected = await page
    .getByRole('textbox', { name: /^The text of/ })
    .evaluate((node: HTMLTextAreaElement) => node.value.slice(node.selectionStart, node.selectionEnd))
  expect(selected).toBe('The lamps along the quay were already lit')

  // On a phone.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(auditUrl)
  await expect(page.getByRole('heading', { level: 1, name: 'Audit of “The storm comes in”' })).toBeVisible()
  await shot(page, '05-phone-audit-results')
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(390)
  await page.goto(`/projects/${PROJECT}/plan/${scenes[1].id}/write`)
  await page.getByRole('group', { name: 'What the assistant checks' }).scrollIntoViewIfNeeded()
  await shot(page, '06-phone-studio-choices', false)
})
