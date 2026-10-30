import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Story time in a scene's context, summaries of whole beats and the ends of other recent
// scenes (CTX-004, CTX-006, CTX-006A, CTX-007), in a real browser against a real backend
// with no AI provider. The shared story is used by other specs, so every name here is
// its own. Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/context-${name}.png`, fullPage })
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
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

test('a scene’s context weighs story time, summarises earlier beats and leaves later scenes out', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ilse', 'Character', 'Ilse Varrow'],
    ['point', 'Location', 'Gull Point'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const part = async (title: string) =>
    (await call(page, 'POST', '/plan', { kind: 'beat', title, expected_revision: await head(page) })).id as string
  const scene = async (title: string, parent: string, at: number) => {
    const made = await call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      summary: `${title} happens.`,
      parent_id: parent,
      refs: [
        { entity_id: ids.ilse, role: 'point_of_view' },
        { entity_id: ids.point, role: 'place' },
      ],
      expected_revision: await head(page),
    })
    await call(page, 'PUT', `/plan/${made.id}/when`, {
      when: { kind: 'at', at },
      expected_revision: await head(page),
    })
    return made.id as string
  }
  const write = (id: string, content: string) =>
    call(page, 'PUT', `/plan/${id}/studio/text`, { content, base_version_id: null })

  // Beat one: two written scenes with kept summaries, early in the story's time.
  const first = await part('Gull Point: the wreck')
  for (const [title, at, words] of [
    ['Gull Point 1.1', 1, 'Ilse Varrow finds the wreck at Gull Point.'],
    ['Gull Point 1.2', 2, 'Ilse Varrow tells nobody what she found.'],
  ] as const) {
    const id = await scene(title, first, at)
    await write(id, `${words} The gulls circle.`)
    await call(page, 'PUT', `/plan/${id}/summary`, { body: words })
  }
  // Beat two: a jump forward the reader meets first, then the story's present.
  const second = await part('Gull Point: the salvage')
  const ahead = await scene('Gull Point 2.1, years later', second, 40)
  await write(ahead, 'Ilse Varrow, old now, sold the last of the salvage at Gull Point.')
  for (const n of [2, 3, 4, 5]) await scene(`Gull Point 2.${n}`, second, n + 1)
  await scene('Gull Point 2.6', second, 7)

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/projects/${PROJECT}/plan`)
  const row = page.getByRole('listitem').filter({ hasText: 'Gull Point 2.6' }).last()
  await row.getByText('Move, plan, remove').click()
  await row.getByRole('button', { name: 'What the assistant would use' }).click()
  const dialog = page.getByRole('dialog', { name: /What the assistant would use for “Gull Point 2.6”/ })

  // The earlier beat comes as a summary worked out from its scenes' summaries.
  const earlier = dialog.getByRole('region', { name: 'What happened in earlier scenes' })
  await expect(
    earlier.getByText(
      'The beat “Gull Point: the wreck”, from its scenes\' summaries: Ilse Varrow finds the wreck at Gull Point. Ilse Varrow tells nobody what she found.',
    ),
  ).toBeVisible()
  await expect(earlier.getByText('Lowered: Further back in the story’s time').first()).toBeVisible()

  // The scene from later in the story's time is left out, said, and can still be chosen.
  const leftOut = dialog.getByRole('region', { name: 'Left out' })
  await expect(leftOut.getByText('From later in the story’s time than this scene')).toBeVisible()
  const later = leftOut.getByRole('listitem').filter({ hasText: 'sold the last of the salvage' })
  await expect(later.getByText(/It happens later in the story.s time than this scene/)).toBeVisible()
  await expectAccessible(page)
  await earlier.scrollIntoViewIfNeeded()
  await shot(page, '1-earlier-beat-1280', false)
  await leftOut.scrollIntoViewIfNeeded()
  await shot(page, '2-later-left-out-1280', false)

  // The beat this scene is in: nothing kept yet, so nothing to give; write one.
  const beatPanel = dialog.getByRole('region', { name: 'Summary of the beat “Gull Point: the salvage”' })
  await expect(beatPanel.getByText(/none of its 6 scenes has a kept summary/)).toBeVisible()
  const wreck = page.getByRole('region', { name: 'Summary of the beat “Gull Point: the wreck”' })
  await expect(wreck).toHaveCount(0)
  await beatPanel.getByRole('button', { name: 'Write a summary of the beat' }).click()
  await beatPanel.getByLabel('Your summary of the whole beat').fill('Ilse salvages the wreck over a hard winter.')
  await beatPanel.getByRole('button', { name: 'Keep my summary' }).click()
  await expect(beatPanel.getByText('Written by you')).toBeVisible()
  await beatPanel.scrollIntoViewIfNeeded()
  await shot(page, '3-beat-summary-kept-1280', false)

  // Choose the later scene after all: it is given.
  await later.getByRole('button', { name: 'Use it' }).click()
  await expect(
    dialog.getByRole('region', { name: 'How other recent scenes end' }).getByText(/sold the last of the salvage/),
  ).toBeVisible()
  await expectAccessible(page)

  // On a phone, and in the dark.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  // The theme is read once, when the page loads; set it as the theme switch would.
  await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'))
  await dialog.getByRole('region', { name: 'How other recent scenes end' }).scrollIntoViewIfNeeded()
  await expectAccessible(page)
  await shot(page, '4-context-390-dark', false)
  await page.evaluate(() => (document.documentElement.dataset.theme = 'light'))
  await page.emulateMedia({ colorScheme: 'light' })
  await page.setViewportSize({ width: 1280, height: 900 })
})
