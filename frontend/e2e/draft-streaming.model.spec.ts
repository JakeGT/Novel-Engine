import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Drafts shown as they are written (SCN-002), stopped part way, and what will be sent and
// where before a large request (CTX-013), against a real backend and the local stand-in
// model (scripts/e2e/fake_model.py), which streams and, asked to "write it slowly", waits
// between pieces. Set E2E_SHOTS to a folder to keep screenshots for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = false) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(300)
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

test('a draft streams as it is written, can be stopped part way, and a large request says what goes where', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['odile', 'Character', 'Odile Brask'],
    ['tower', 'Location', 'The Salt Tower'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Low Tide at the Salt Tower',
    summary: 'Odile waits for a boat that does not come.',
    refs: [
      { entity_id: ids.odile, role: 'point_of_view' },
      { entity_id: ids.tower, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  await expect(page.getByRole('heading', { level: 1, name: 'Low Tide at the Salt Tower' })).toBeVisible()

  // Before anything is sent: what it reads, what it costs, and where it goes.
  await page.getByRole('button', { name: 'Draft with the assistant' }).click()
  const form = page.getByRole('region', { name: 'Draft with the assistant' })
  await expect(form.getByText(/It goes to the model server at 127\.0\.0\.1/)).toBeVisible()
  await expect(form.getByText(/on this site’s own network, not to any company\./)).toBeVisible()
  await shot(page, 'A-1-draft-form-where-it-goes')

  // Written slowly by the stand-in, so the words can be watched arriving.
  await form.getByLabel('Anything it should do (optional)').fill('Write it slowly.')
  await form.getByRole('button', { name: 'Write a draft' }).click()
  const progress = page.getByRole('region', { name: 'Work in progress' })
  const writing = progress.getByRole('figure', { name: 'Draft 1 as it is written' })
  await expect(writing).toContainText('Wren Hollis crossed the yard', { timeout: 20_000 })
  await expect(writing).toContainText(/Draft 1, \d+ words so far\. It is not kept until it is finished and you keep it\./)
  await shot(page, 'A-2-draft-streaming-1280')
  await expectAccessible(page)

  // On a phone, in the dark: the page opened afresh picks the stream up again.
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  const again = page.getByRole('region', { name: 'Work in progress' }).getByRole('figure', { name: 'Draft 1 as it is written' })
  await expect(again).toContainText('Wren Hollis crossed the yard', { timeout: 20_000 })
  await again.scrollIntoViewIfNeeded()
  await shot(page, 'A-3-draft-streaming-390-dark')
  await expectAccessible(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to light' }).click()

  // Stopped part way: nothing is kept, and it says so.
  await progress.getByRole('button', { name: 'Stop' }).click()
  await expect(page.getByText('Stopping: this draft will not be kept.').or(page.getByText(/^Stopped\./))).toBeVisible()
  await expect(page.getByText('Nothing was kept from it. Your text is unchanged.')).toBeVisible({ timeout: 20_000 })
  await shot(page, 'A-4-draft-stopped')
  const jobs = (await call(page, 'GET', `/plan/${scene.id}/studio`)).jobs as { status: string }[]
  expect(jobs[0].status).toBe('cancelled')
  expect((await call(page, 'GET', `/plan/${scene.id}/studio`)).drafts).toHaveLength(0)

  // At the stand-in's own speed it streams and is stored whole.
  await page.getByRole('button', { name: 'Dismiss' }).click()
  await page.getByRole('button', { name: 'Draft with the assistant' }).click()
  await form.getByLabel('Anything it should do (optional)').fill('')
  await form.getByRole('button', { name: 'Write a draft' }).click()
  await expect(page.getByText('A draft is ready.')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Read the draft' }).click()
  await expect(page.getByText(/One was missing, and she said nothing about it/).first()).toBeVisible()

  // Above the size the author asked to be asked about: the question says what goes where.
  await call(page, 'PUT', '/assistant-settings', { confirm_above_tokens: 1000 })
  try {
    await page.getByRole('button', { name: 'Draft with the assistant' }).click()
    await form.getByRole('button', { name: '3', exact: true }).click()
    await form.getByRole('button', { name: 'Write 3 drafts' }).click()
    const dialog = page.getByRole('dialog', { name: /Use about [\d,]+ tokens\?/ })
    await expect(dialog).toBeVisible()
    const sending = dialog.getByRole('region', { name: 'What will be sent' })
    await expect(sending).toContainText('Goes to')
    await expect(sending).toContainText('the model server at 127.0.0.1')
    await expect(sending).toContainText(/From your story\s*About [\d,]+ words/)
    await expect(sending).toContainText('It goes to a model on this site’s own network, not to any company.')
    await expectAccessible(page)
    await shot(page, 'A-5-ask-first-what-goes-where-1280')
    await page.setViewportSize({ width: 390, height: 844 })
    await shot(page, 'A-6-ask-first-what-goes-where-390')
    await page.setViewportSize({ width: 1280, height: 900 })
    await dialog.getByRole('button', { name: 'Don’t send' }).click()
    await expect(dialog).toBeHidden()
    // Nothing was sent: still the one draft from before.
    expect((await call(page, 'GET', `/plan/${scene.id}/studio`)).drafts).toHaveLength(1)
  } finally {
    await call(page, 'PUT', '/assistant-settings', { confirm_above_tokens: null })
  }
})
