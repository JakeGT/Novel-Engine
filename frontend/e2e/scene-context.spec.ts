import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// What the assistant would use for a scene, in a real browser against a real backend,
// with no AI provider: every item says why, and the author leaves one out and back in.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`

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

test('the context for a scene says why for each thing, and the author decides', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Hollis'],
    ['mill', 'Location', 'The Old Mill'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.wren,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.mill, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/texts', {
    title: 'Notebook',
    content: 'Wren Hollis swept the floor of The Old Mill while the wheel groaned.',
  })
  await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Flour and Dust',
    refs: [
      { entity_id: ids.wren, role: 'point_of_view' },
      { entity_id: ids.mill, role: 'place' },
    ],
    expected_revision: await head(page),
  })

  await page.goto(`/projects/${PROJECT}/plan`)
  const row = page.getByRole('listitem').filter({ hasText: 'Flour and Dust' })
  await row.getByText('Move, plan, remove').click()
  await row.getByRole('button', { name: 'What the assistant would use' }).click()
  const dialog = page.getByRole('dialog', { name: /What the assistant would use for “Flour and Dust”/ })
  await expect(dialog.getByText(/Wren Hollis is the scene.s point of view/)).toBeVisible()
  await expect(dialog.getByRole('listitem').filter({ hasText: /From “Notebook”: Wren Hollis swept the floor/ })).toBeVisible()
  await expectAccessible(page)

  const fact = dialog.getByRole('listitem').filter({ hasText: 'Wren Hollis is located at The Old Mill.' })
  await fact.getByRole('button', { name: 'Don’t use' }).click()
  const leftOut = dialog.getByRole('region', { name: 'Left out' })
  await expect(leftOut.getByText('You said not to use these')).toBeVisible()
  await leftOut.getByRole('button', { name: 'Use it again' }).click()
  await expect(dialog.getByRole('region', { name: 'Left out' })).toHaveCount(0)
  await expectAccessible(page)
})
