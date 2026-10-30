import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The check remainders (batch 9 B), in a real browser against a real backend with no AI
// provider: a height and a scar on the other side are notes quoting the story bible
// (CON-004); one kind of finding is dismissed wherever it is about one character, holds in
// another scene, and is undone everywhere (CON-016); Check history says how long each check
// took (CON-001). Set E2E_SHOTS to a folder to keep a screenshot of each step.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/checks-${name}.png`, fullPage })
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

async function signIn(page: Page) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
}

async function write(page: Page, planId: string, words: string, version: number) {
  await page.goto(`/projects/${PROJECT}/plan/${planId}/write`)
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill(words)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(new RegExp(`Saved as version ${version}`))).toBeVisible()
}

test('identity notes, a dismissal for one character across scenes, and how long checks took', async ({ page }) => {
  await signIn(page)
  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['isolde', 'Character', 'Isolde Varrow'],
    ['brannoc', 'Character', 'Brannoc Fell'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.isolde,
      predicate: 'HAS_DETAIL',
      object: { kind: 'structured', value: { appearance: 'Tall, with a scar across her left cheek.' } },
    },
    expected_revision: await head(page),
  })
  const add = async (title: string) =>
    (
      await call(page, 'POST', '/plan', {
        kind: 'scene',
        title,
        summary: 'Isolde waits for the bell.',
        refs: [
          { entity_id: ids.isolde, role: 'point_of_view' },
          { entity_id: ids.brannoc, role: 'participant' },
        ],
        expected_revision: await head(page),
      })
    ).id as string
  const first = await add('The Bell Tower at Varrow')
  const second = await add('The Long Stair at Varrow')

  await write(
    page,
    first,
    'Isolde Varrow pulled up her hood against the rain. Isolde Varrow was a small woman, and quick. ' +
      'Isolde Varrow touched the scar on her right cheek. Brannoc Fell did not come.',
    1,
  )
  const checks = page.getByRole('region', { name: 'Checks', exact: true })
  await expect(checks).toContainText('Here Isolde Varrow has “small”, but the story bible says: “Tall, with a scar across her left cheek.”')
  await expect(checks).toContainText('Here Isolde Varrow has “scar on her right cheek”')

  // Dismissed wherever it is about Isolde.
  const scar = /^Dismiss: Here Isolde Varrow has “scar on her right cheek”/
  await checks.getByRole('button', { name: scar }).click()
  const form = checks.getByRole('group', { name: scar })
  await form.getByLabel('Applies to').selectOption({ label: 'Wherever it is about Isolde Varrow' })
  await expect(form).toContainText('Every finding of this kind there is dismissed, including any found later')
  await form.getByRole('button', { name: 'Not a problem' }).click()
  await shot(page, '01-applies-to')
  await expectAccessible(page)
  await form.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(checks.getByText(/Applies to: A scar or mark on the other side, wherever it is about Isolde Varrow/).first()).toBeVisible()

  // How long each check took, in Check history.
  const history = page.getByRole('region', { name: 'Check history' })
  await history.getByText(/^How long it took:/).first().click()
  await expect(history.getByRole('cell', { name: 'Reading what the checks need from the story' }).first()).toBeVisible()
  await expect(history.getByRole('cell', { name: 'How people look and how old they are' }).first()).toBeVisible()
  await expect(history.getByRole('region', { name: 'Dismissed beyond one scene' })).toContainText(
    'A scar or mark on the other side, wherever it is about Isolde Varrow',
  )
  await history.getByText(/^How long it took:/).first().scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await shot(page, '02-history-timings', false)
  await expectAccessible(page)

  // Another scene, written later: the same kind of finding about Isolde is dismissed there too.
  await write(page, second, 'Isolde Varrow pulled up her hood. The scar on Isolde Varrow\'s right cheek ached in the cold.', 1)
  await checks.getByText('Dismissed: 1 check').click()
  await expect(checks).toContainText('Applies to: A scar or mark on the other side, wherever it is about Isolde Varrow')

  // At phone width, in the dark.
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await checks.getByText('Dismissed: 1 check').click()
  await checks.getByText('Dismissed: 1 check').scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await shot(page, '03-dismissed-phone-dark', false)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await expectAccessible(page)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.reload()

  // Undone everywhere, from this scene.
  await checks.getByText('Dismissed: 1 check').click()
  await checks.getByRole('button', { name: /^Undo everywhere dismissing:/ }).click()
  await expect(checks.getByText(/Dismissed:/)).toHaveCount(0)
  await page.goto(`/projects/${PROJECT}/plan/${first}/write`)
  await expect(checks.getByRole('button', { name: scar })).toBeVisible()
})
