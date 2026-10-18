import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// How much the assistant does and spends on its own (CON-009, OPS-002), in a real browser
// against the local stand-in model: the author chooses "Balanced" checking with its cost
// stated, keeps a draft and the scene is checked on its own; then a request above the size
// they set to be asked about waits for them to agree.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (!SHOTS) return
  // Dialogs fade in; the picture is taken once they have.
  await page.waitForTimeout(450)
  await page.screenshot({ path: `${SHOTS}/c-spending-${name}.png`, fullPage })
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

// The settings are the story's own: put them back so later tests start from the defaults.
test.afterEach(async ({ page }) => {
  await call(page, 'PUT', '/assistant-settings', {
    check_profile: 'on_request',
    scene_tokens: 40000,
    check_minutes: 15,
    confirm_above_tokens: null,
    conversation_tokens: null,
  })
})

test('checking on its own after a kept draft, and asking before a large request', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const wren = (await call(page, 'POST', '/entities', { type: 'Character', name: 'Ada Quill', expected_revision: await head(page) })).id
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Chalk and Rain',
    summary: 'Ada finds a sack missing and keeps it to herself.',
    refs: [{ entity_id: wren, role: 'point_of_view' }],
    expected_revision: await head(page),
  })

  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  await expect(page.getByRole('heading', { level: 1, name: 'Chalk and Rain' })).toBeVisible()
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill('Ada Quill swept the floor and counted the sacks twice.')
  await page.getByRole('button', { name: /^Save/ }).first().click()
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()
  await expect(page.getByText('Checks with the assistant only when you ask.')).toBeVisible()

  // The choice, with what each costs for this scene, before choosing.
  await page.getByRole('button', { name: 'Change how much it checks' }).click()
  const dialog = page.getByRole('dialog', { name: 'Checking on its own' })
  await expect(dialog.getByText(/tokens each check of this scene/).first()).toBeVisible()
  await expect(dialog.getByText('Costs nothing on its own.')).toBeVisible()
  await dialog.getByRole('radio', { name: /Balanced/ }).check()
  await shot(page, '01-checking-dialog', false)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Save how much it checks' }).click()
  await expect(dialog.getByText('Saved. Balanced checking is on for this story.')).toBeVisible()
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByText(/checks on its own after you keep a draft\./)).toBeVisible()

  // Keeping a draft starts a check on its own; saving by hand does not.
  await page.getByRole('button', { name: 'Draft with the assistant' }).click()
  await page.getByRole('button', { name: 'Write a draft' }).click()
  await expect(page.getByText('A draft is ready.')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Read the draft' }).click()
  await page.getByRole('button', { name: /^Keep/ }).first().click()
  await expect(page.getByText(/Checked on its own, as this story is set to\./)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('The scene is set in the morning, but the lamps are lit.')).toBeVisible()
  await expect(page.getByText(/of 40,000 tokens used for this scene today/)).toBeVisible()
  await shot(page, '02-checked-on-its-own')
  await expectAccessible(page)

  // Asking first: the story is set to ask above 1,000 tokens, so a draft waits for a yes.
  const settings = await call(page, 'GET', '/assistant-settings')
  await call(page, 'PUT', '/assistant-settings', {
    check_profile: settings.check_profile,
    scene_tokens: settings.scene_tokens,
    check_minutes: settings.check_minutes,
    confirm_above_tokens: 1000,
    conversation_tokens: null,
  })
  await page.reload()
  await page.getByRole('button', { name: 'Draft with the assistant' }).click()
  await page.getByRole('button', { name: 'Write a draft' }).click()
  const ask = page.getByRole('dialog', { name: /^Use about [\d,]+ tokens\?$/ })
  await expect(ask).toBeVisible()
  await expect(ask.getByText(/Writing this draft will use about [\d,]+ tokens, more than the 1,000/)).toBeVisible()
  await expect(ask.getByRole('button', { name: 'Don’t send' })).toBeFocused()
  await shot(page, '03-ask-first', false)
  await expectAccessible(page)

  // Saying no sends nothing.
  await ask.getByRole('button', { name: 'Don’t send' }).click()
  await expect(page.getByText(/Not sent, as you chose\. Nothing was spent/)).toBeVisible()

  // Saying yes sends it.
  await page.getByRole('button', { name: 'Write a draft' }).click()
  await page.getByRole('dialog', { name: /^Use about/ }).getByRole('button', { name: /^Send it/ }).click()
  await expect(page.getByText('A draft is ready.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/It used [\d,]+ tokens/).first()).toBeVisible()

  // The same limits on the AI assistant page, for the whole story.
  await page.goto(`/projects/${PROJECT}/assistant`)
  const card = page.getByRole('region', { name: 'Spending in this story' })
  await expect(card.getByLabel('Ask me first when one request would use more than')).toHaveValue('1000')
  await card.getByLabel('Most one conversation may use').selectOption('50000')
  await card.getByRole('button', { name: 'Save spending limits' }).click()
  await expect(card.getByText('Saved.')).toBeVisible()
  await shot(page, '04-assistant-page')
  await expectAccessible(page)

  // At phone width.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  await expect(page.getByRole('heading', { level: 1, name: 'Chalk and Rain' })).toBeVisible()
  await page.getByRole('button', { name: 'Change how much it checks' }).click()
  await expect(page.getByRole('dialog', { name: 'Checking on its own' })).toBeVisible()
  await shot(page, '05-phone-checking', false)
  await page.getByRole('dialog', { name: 'Checking on its own' }).getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByText(/checks on its own after you keep a draft/).scrollIntoViewIfNeeded()
  await shot(page, '06-phone-studio')
})
