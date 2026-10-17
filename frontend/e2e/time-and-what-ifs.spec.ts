import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Phase 3 in a real browser against a real backend, with no AI provider: an event is
// placed in story time, a fact becomes a secret and a character is shown not to know
// it, and a what-if is started, worked in, compared and brought back for review.

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

async function call(page: Page, method: string, path: string, data?: unknown, branch?: string) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data,
    headers: {
      'X-CSRF-Token': csrf,
      'Content-Type': 'application/json',
      ...(branch ? { 'X-Story-Branch': branch } : {}),
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

const head = async (page: Page, branch?: string) =>
  (await call(page, 'GET', '/projection', undefined, branch)).head_revision as number

test('story time, secrets and a what-if, by hand', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['mara', 'Character', 'Mara Quill'],
    ['tobin', 'Character', 'Tobin Reed'],
    ['guild', 'Faction', 'The Lamplighters'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.mara,
      predicate: 'MEMBER_OF',
      object: { kind: 'entity_ref', entity_id: ids.guild, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/events', {
    kind: 'happening',
    name: 'The lamps go dark',
    expected_revision: await head(page),
  })

  // Place the event in story time.
  await page.goto(`/projects/${PROJECT}/timeline`)
  await expect(page.getByRole('heading', { level: 1, name: 'Timeline' })).toBeVisible()
  await page.getByRole('listitem').filter({ hasText: 'The lamps go dark' }).getByRole('button', { name: 'Place in time' }).click()
  const dialog = page.getByRole('dialog', { name: /When did “The lamps go dark” happen/ })
  await dialog.getByRole('radio', { name: 'At a point' }).check()
  await dialog.getByLabel('When: year').fill('12')
  await expect(dialog.getByText('Reads as: year 12.')).toBeVisible()
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Save when it happened' }).click()
  await expect(page.getByRole('list').filter({ hasText: 'The lamps go dark' }).getByText('year 12')).toBeVisible()
  await expectAccessible(page)

  // Make Mara's membership a secret; Tobin does not know it.
  await page.goto(`/projects/${PROJECT}/entries/${ids.mara}`)
  await page.getByRole('button', { name: /When and who knows: Mara Quill .*The Lamplighters/ }).click()
  const details = page.getByRole('dialog', { name: 'When it is true, and who knows it' })
  await details.getByRole('radio', { name: /A secret/ }).check()
  await expectAccessible(page)
  await details.getByRole('button', { name: 'Save when and who knows' }).click()
  await expect(details).toHaveCount(0)
  await expect(page.getByText('Secret', { exact: true }).first()).toBeVisible()
  await page.goto(`/projects/${PROJECT}/entries/${ids.tobin}`)
  const knows = page.getByRole('region', { name: 'What Tobin Reed knows' })
  await expect(knows.getByText('Secrets Tobin Reed does not know')).toBeVisible()
  await expect(knows.getByText(/Mara Quill is a member of The Lamplighters/)).toBeVisible()
  await expectAccessible(page)

  // Start a what-if and work in it.
  await page.goto(`/projects/${PROJECT}/what-ifs`)
  await page.getByLabel('What if…').fill('What if the lamps never went dark?')
  await page.getByRole('button', { name: 'Start and work in it' }).click()
  const band = page.getByRole('region', { name: 'Working in a what-if' })
  await expect(band).toContainText('What if the lamps never went dark?')
  await expectAccessible(page)
  const whatIf = (await call(page, 'GET', '/what-ifs')).items[0]
  await call(page, 'POST', '/entities', {
    type: 'Character',
    name: 'Wick',
    expected_revision: await head(page, whatIf.id),
  }, whatIf.id)
  await page.goto(`/projects/${PROJECT}/bible?view=all`)
  await expect(page.getByText('Wick').first()).toBeVisible()

  // Back in the story, Wick does not exist.
  await band.getByRole('button', { name: 'Back to the story' }).click()
  await expect(band).toHaveCount(0)
  await page.reload()
  await expect(page.getByText('Mara Quill').first()).toBeVisible()
  await expect(page.getByText('Wick')).toHaveCount(0)

  // Compare, and bring it back as a change to review.
  await page.goto(`/projects/${PROJECT}/what-ifs/${whatIf.id}`)
  const mine = page.getByRole('region', { name: 'Changed in this what-if' })
  await expect(mine.getByText('Wick')).toBeVisible()
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Suggest these changes for my story' }).click()
  await expect(page).toHaveURL(/\/review\//)
  await expect(page.getByText(/Wick/).first()).toBeVisible()
})
