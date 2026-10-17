import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The scene studio in a real browser against a real backend, with a local stand-in model
// (scripts/e2e/fake_model.py): write, draft with the assistant, keep the draft, check it.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
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

test('write a scene, draft it with the assistant, keep the draft and check it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
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
  await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Flour and Dust',
    summary: 'Wren finds a sack missing and keeps it to herself.',
    refs: [
      { entity_id: ids.wren, role: 'point_of_view' },
      { entity_id: ids.mill, role: 'place' },
    ],
    expected_revision: await head(page),
  })

  // From the plan, straight into the scene.
  await page.goto(`/projects/${PROJECT}/plan`)
  const row = page.getByRole('listitem').filter({ hasText: 'Flour and Dust' })
  await row.getByRole('link', { name: /Write this scene/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Flour and Dust' })).toBeVisible()
  await expect(page.getByText(/Nothing saved yet/)).toBeVisible()
  await shot(page, '01-empty')
  await expectAccessible(page)

  // Writing by hand.
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill('Wren Hollis swept the floor of The Old Mill. It was a testament to her patience.')
  await shot(page, '02-unsaved')
  await page.getByRole('button', { name: /^Save/ }).first().click()
  await expect(page.getByText(/stock phrasing/)).toBeVisible()
  await shot(page, '03-saved-with-checks')

  // Drafting with the assistant: the cost is shown before anything is sent.
  await page.getByRole('button', { name: 'Draft with the assistant' }).click()
  await expect(page.getByText(/words|tokens/).first()).toBeVisible()
  await shot(page, '04-draft-form')
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Write a draft' }).click()
  await expect(page.getByText('A draft is ready.')).toBeVisible({ timeout: 30_000 })
  await shot(page, '05a-draft-ready')
  await page.getByRole('button', { name: 'Read the draft' }).click()
  await expect(page.getByText(/One was missing, and she said nothing about it/).first()).toBeVisible()
  await shot(page, '05-draft-waiting')
  await expectAccessible(page)

  // Keeping it makes it the newest version, written by the assistant.
  await page.getByRole('button', { name: /^Keep/ }).first().click()
  await expect(editor).toHaveValue(/The lamps along the quay were already lit/)
  await expect(page.getByRole('cell', { name: /assistant/i }).first()).toBeVisible()
  await shot(page, '06-kept')

  // A check with the assistant keeps only notes that quote the text.
  await page.getByRole('button', { name: /Check with the assistant/ }).first().click()
  await expect(page.getByText('The scene is set in the morning, but the lamps are lit.')).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('This note cannot be pinned to the text.')).toHaveCount(0)
  await shot(page, '07-checked')
  await expectAccessible(page)

  // How you write.
  await page.getByRole('button', { name: /How you write/ }).first().click()
  const voice = page.getByRole('dialog', { name: 'How you write' })
  await expect(voice).toBeVisible()
  await page.waitForTimeout(400)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/08-voice.png` })
  await expectAccessible(page)
  await voice.getByRole('button', { name: /Close/ }).first().click()

  // The scene's words are among the author's writing, and change only in the studio.
  await page.getByRole('link', { name: 'Your writing' }).click()
  await page.getByRole('link', { name: /Flour and Dust/ }).first().click()
  await expect(page.getByRole('link', { name: /Write it in the scene.s studio/ })).toBeVisible()
  await expect(page.getByText('Change or remove')).toHaveCount(0)
  await shot(page, '09-text-page')
  await page.getByRole('link', { name: /Write it in the scene.s studio/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Flour and Dust' })).toBeVisible()
})
