import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// What is said and how people take it (KNW-005, KNW-006), in a real browser against a real
// backend, with no AI provider: a character's trust is set on their page; a telling suggests
// a belief for each hearer by that trust, with the reason, and nothing changes before review;
// something a character says is recorded as said, not true, beside the facts, and made a
// fact only through review. Set E2E_SHOTS to a folder to keep a screenshot of each step.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/B-${name}.png`, fullPage })
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

test('trust shapes what a telling suggests, and what is said stays apart from the facts', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ada', 'Character', 'Ada Quill'],
    ['bram', 'Character', 'Bram Ostler'],
    ['cass', 'Character', 'Cass Merrow'],
    ['mill', 'Location', 'The Old Mill'],
    ['ford', 'Location', 'Greyford'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.ada,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.mill, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })

  // Bram doubts what people tell him, and does not believe Ada at all.
  await page.goto(`/projects/${PROJECT}/entries/${ids.bram}`)
  const trust = page.getByRole('region', { name: 'How Bram Ostler takes what others say' })
  await expect(trust).toContainText('Nobody in particular')
  await trust.getByLabel('When anyone tells Bram Ostler something').selectOption('doubts')
  await trust.getByRole('combobox', { name: 'Someone in particular' }).selectOption({ label: 'Ada Quill' })
  await trust.getByRole('combobox', { name: 'What they make of it' }).selectOption('disbelieves')
  await trust.getByRole('button', { name: 'Add' }).click()
  await trust.getByRole('button', { name: 'Save' }).click()
  await expect(trust.getByRole('status')).toHaveText('Saved.')
  await expect(
    trust.getByRole('combobox', { name: 'What Bram Ostler makes of what Ada Quill says' }),
  ).toHaveValue('disbelieves')
  await trust.scrollIntoViewIfNeeded()
  await shot(page, 'trust')
  await expectAccessible(page)

  // Ada tells Bram and Cass where she is: only Cass is suggested a belief, and why.
  await page.goto(`/projects/${PROJECT}/entries/${ids.ada}`)
  const before = await head(page)
  await page.getByRole('region', { name: 'What Ada Quill knows' }).getByRole('button', { name: 'Tell someone something' }).click()
  const tell = page.getByRole('dialog', { name: 'Tell someone something' })
  await tell.getByRole('group', { name: 'Who is told' }).getByLabel('Bram Ostler').check()
  await tell.getByRole('group', { name: 'Who is told' }).getByLabel('Cass Merrow').check()
  await tell.getByRole('combobox', { name: 'Which fact' }).selectOption({ label: 'Ada Quill is located at The Old Mill' })
  await expectAccessible(page)
  await tell.getByRole('button', { name: 'Suggest what they believe' }).click()
  const told = page.getByRole('dialog', { name: 'Suggested for review' })
  const rows = told.getByRole('list', { name: 'What each person would believe' })
  await expect(rows).toContainText('Bram Ostler was told but does not believe Ada Quill, so no belief is added.')
  await expect(rows).toContainText('Cass Merrow was told by Ada Quill')
  await expect(told).toContainText('Nothing is in your story until you approve it.')
  expect(await head(page)).toBe(before)
  await shot(page, 'tell-suggested', false)
  await expectAccessible(page)
  await told.getByRole('link', { name: 'Review the suggestion' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Changes from what people were told or saw')
  await expect(page.getByText(/Cass Merrow believes what people tell them, so believes it firmly/).first()).toBeVisible()
  await shot(page, 'tell-review')
  await expectAccessible(page)
  await page.getByRole('button', { name: /Approve and add to your story/ }).click()
  await expect(page.getByRole('dialog', { name: 'Added to your story' })).toBeVisible()

  // Bram says Ada is at Greyford: said, not true; the facts say otherwise.
  await page.goto(`/projects/${PROJECT}/entries/${ids.ada}`)
  const said = page.getByRole('region', { name: 'Said in the story' })
  await expect(said).toContainText('Nothing recorded yet.')
  await said.getByRole('button', { name: 'Record something said' }).click()
  const record = page.getByRole('dialog', { name: 'Record something said' })
  await record.getByLabel('Who says it').selectOption({ label: 'Bram Ostler' })
  await record.getByLabel('Something else, which may not be true').check()
  await record.getByRole('combobox', { name: 'How the sentence continues' }).selectOption({ label: 'is located at' })
  await record.getByRole('combobox', { name: 'What Ada Quill is located at' }).selectOption({ label: 'Greyford (Location)' })
  await record.getByRole('group', { name: 'Who hears it (optional)' }).getByLabel('Cass Merrow').check()
  await expectAccessible(page)
  await shot(page, 'record-said', false)
  await record.getByRole('button', { name: 'Record what is said' }).click()
  await page.getByRole('dialog', { name: 'Recorded' }).getByRole('button', { name: 'Done' }).click()
  await expect(said).toContainText('Bram Ostler says Ada Quill is located at Greyford')
  await expect(said).toContainText('Your facts say otherwise')
  await expect(said).toContainText('Heard by Cass Merrow')
  // Nobody came to believe it because it was said.
  const cass = await call(page, 'GET', `/beliefs?holder_entity_id=${ids.cass}`)
  expect(cass.items).toHaveLength(1)
  await said.scrollIntoViewIfNeeded()
  await shot(page, 'said-on-entry')
  await expectAccessible(page)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(said).toContainText('Your facts say otherwise')
  await said.scrollIntoViewIfNeeded()
  await shot(page, 'said-390', false)
  await expectAccessible(page)
  await page.setViewportSize({ width: 1280, height: 800 })

  await said.getByRole('button', { name: /^Make it a fact: Bram Ostler says/ }).click()
  await expect(page).toHaveURL(/\/review\//)
  await expect(page.getByText(/Made a fact from what Bram Ostler says/).first()).toBeVisible()
  await shot(page, 'promote-review')
  await expectAccessible(page)
})
