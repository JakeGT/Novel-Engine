import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Every page of a project with something on it, checked with axe in light and in dark.
// Set E2E_SHOTS to a folder to keep a screenshot of each page in each theme.

// A local install has one story, shared with the other browser tests: every name here is
// this test's own, so nothing it adds collides with what they add.
const PROJECT = 'local-project'
const SHOTS = process.env.E2E_SHOTS

async function violations(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  return results.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} [${violation.nodes.map((node) => node.target.join(' ')).join(' | ')}]`,
  )
}

async function call(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`/api/v1/projects/${PROJECT}${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

test('every page passes the accessibility checks in light and dark', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
  // A small story: two entries, a fact, a scene with words, a text, a rule-free what-if.
  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ada', 'Character', 'Ada Quill'],
    ['tower', 'Location', 'The Signal Tower'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.ada,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.tower, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Salt and Lamplight',
    summary: 'Ada keeps the light.',
    refs: [{ entity_id: ids.ada, role: 'point_of_view' }],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${scene.id}/studio/text`, {
    content: 'Ada Quill climbed The Signal Tower at dusk.',
    base_version_id: null,
  })
  const text = await call(page, 'POST', '/texts', { title: 'Logbook', content: 'Ada Quill kept the log.' })
  const whatIf = await call(page, 'POST', '/what-ifs', { name: 'What if the tower fell' })

  const pages: [string, string][] = [
    ['home', `/projects/${PROJECT}`],
    ['bible', `/projects/${PROJECT}/bible`],
    ['entry', `/projects/${PROJECT}/entries/${ids.ada}`],
    ['review', `/projects/${PROJECT}/review`],
    ['kept-for-you', `/projects/${PROJECT}/review/kept`],
    ['plan', `/projects/${PROJECT}/plan`],
    ['studio', `/projects/${PROJECT}/plan/${scene.id}/write`],
    ['rules', `/projects/${PROJECT}/rules`],
    ['timeline', `/projects/${PROJECT}/timeline`],
    ['what-ifs', `/projects/${PROJECT}/what-ifs`],
    ['what-if', `/projects/${PROJECT}/what-ifs/${whatIf.id}`],
    ['texts', `/projects/${PROJECT}/texts`],
    ['text', `/projects/${PROJECT}/texts/${text.id}`],
    ['names', `/projects/${PROJECT}/names`],
    ['history', `/projects/${PROJECT}/history`],
    ['customize', `/projects/${PROJECT}/customize`],
    ['media', `/projects/${PROJECT}/media`],
    ['assistant', `/projects/${PROJECT}/assistant`],
    ['people', `/projects/${PROJECT}/people`],
    ['projects', '/'],
  ]
  const found: string[] = []
  for (const theme of ['light', 'dark']) {
    if (theme === 'dark') {
      await page.goto(`/projects/${PROJECT}`)
      await page.getByRole('button', { name: 'Switch to dark' }).click()
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    }
    for (const [name, path] of pages) {
      await page.goto(path)
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
      await expect(page.getByText(/^(Loading|Opening)/)).toHaveCount(0)
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/sweep-${theme}-${name}.png`, fullPage: true })
      found.push(...(await violations(page)).map((item) => `${theme} ${name}: ${item}`))
    }
  }
  expect(found).toEqual([])
})

test('a scene can be opened, written and saved with the keyboard alone', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
  await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Keys at Night',
    expected_revision: await head(page),
  })
  await page.goto(`/projects/${PROJECT}/plan`)
  // Tab to the scene's "Write this scene" and open it with Enter.
  const write = page.getByRole('link', { name: 'Write this scene: Keys at Night' })
  for (let presses = 0; presses < 400; presses += 1) {
    if (await write.evaluate((node) => node === document.activeElement)) break
    await page.keyboard.press('Tab')
  }
  await expect(write).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Keys at Night' })).toBeVisible()

  const editor = page.getByRole('textbox', { name: /^The text of/ })
  for (let presses = 0; presses < 40; presses += 1) {
    if (await editor.evaluate((node) => node === document.activeElement)) break
    await page.keyboard.press('Tab')
  }
  await expect(editor).toBeFocused()
  await page.keyboard.type('Ada Quill found the keys under the lamp.')
  await page.keyboard.press('Control+s')
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()
  await expect(page.getByRole('cell', { name: '1 · current' })).toBeVisible()
})
