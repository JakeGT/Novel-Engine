import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Bringing in the author's own prose, in a real browser against a real backend,
// with no AI provider: paste a text, answer who a marked name is by adding it as
// an entry, and see that entry lead back to the words it came from.

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

test('a pasted text names its people, and a marked name becomes an entry that points back', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  // Not in a new project's menu yet, but one click away.
  await page.getByText('More', { exact: true }).first().click()
  await page.getByRole('link', { name: 'Your writing' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Your writing' })).toBeVisible()
  await expectAccessible(page)

  await page.getByRole('button', { name: 'Bring in a text' }).first().click()
  await page.getByLabel(/^What to call it/).fill('The lamp room')
  await page
    .getByLabel(/^The text/)
    .fill('# Night\n\nThe keeper climbed to the lamp room, and @Quillon was already there.\n\nQuillon did not turn round.\n')
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Bring it in' }).click()

  // The words are shown as written, and the marked name is a question, not a guess.
  const words = page.getByRole('article', { name: 'The text' })
  await expect(words).toContainText('@Quillon was already there.')
  const question = page.getByRole('region', { name: 'Who is this?' })
  await expect(question).toContainText('“Quillon”')
  await expectAccessible(page)

  await question.getByRole('button', { name: 'Add as a new entry' }).click()
  await expect(page.getByRole('heading', { name: 'Who is this?' })).toHaveCount(0)
  // Both the marked name and the plain one now lead to the entry.
  await expect(words.getByRole('link', { name: /Quillon/ })).toHaveCount(2)
  await expect(page.getByRole('region', { name: 'Named here' })).toContainText('Character · 2 times')
  await expectAccessible(page)

  // The entry can say where it came from, in the author's own words.
  await words.getByRole('link', { name: /Quillon/ }).first().click()
  await expect(page.getByRole('heading', { level: 1, name: 'Quillon' })).toBeVisible()
  const cited = page.locator('.from-text').first()
  await expect(cited).toContainText('@Quillon')
  await expect(cited.getByRole('link', { name: 'The lamp room' })).toBeVisible()
  await cited.getByRole('link', { name: 'The lamp room' }).click()
  await expect(page.locator('mark#cited')).toHaveText('@Quillon')
})
