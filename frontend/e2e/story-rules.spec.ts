import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// A story rule built by hand, in a real browser against a real backend, with no
// AI provider: it is shown as it will be checked before it is kept, and then
// says whether the story keeps it.

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

test('a rule is built by hand, read back in words, and checked against the story', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  await page.getByText('Pages you have not used yet', { exact: true }).first().click()
  await page.getByRole('link', { name: 'Story rules' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Story rules' })).toBeVisible()
  await expectAccessible(page)

  await page.getByRole('button', { name: /New rule|Write your first rule/ }).first().click()
  const dialog = page.getByRole('dialog', { name: 'New story rule' })
  await dialog.getByLabel(/^The rule, in your own words/).fill('Nobody is in two places at once.')
  await dialog.getByRole('button', { name: 'Build it by hand' }).click()
  await dialog.getByLabel('Which entries the rule is about').selectOption('type:Character')
  await dialog.getByLabel('Must or must not').selectOption('at_most')
  await dialog.getByLabel('What must hold: kind of fact').selectOption('LOCATED_AT')
  await expect(dialog.getByRole('region', { name: 'How it will be checked' })).toContainText(
    'Checked as: Every character can have at most 1 of this: it is located at something.',
  )
  await dialog.getByLabel('A short name for it').fill('One place at a time')
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Keep this rule' }).click()

  const card = page.getByRole('listitem').filter({ hasText: 'One place at a time' })
  await expect(card).toContainText('“Nobody is in two places at once.”')
  await expect(card).toContainText('Holds')
  await expectAccessible(page)

  // Clean up so the shared project is as other tests expect it.
  await card.getByRole('button', { name: 'Remove One place at a time' }).click()
  await page.getByRole('button', { name: 'Remove the rule' }).click()
  await expect(page.getByRole('heading', { name: 'One place at a time' })).toHaveCount(0)
})

async function call(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`/api/v1/projects/local-project${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

test('a rule with an exception, two facts and story time is built by hand and read back', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
  // An entry of this test's own to leave out, whatever else the shared story holds.
  const head = (await call(page, 'GET', '/projection')).head_revision as number
  const wren = await call(page, 'POST', '/entities', { type: 'Character', name: 'Wren Ashdown', expected_revision: head })
  await page.reload()
  await page.getByText('Pages you have not used yet', { exact: true }).first().click()
  await page.getByRole('link', { name: 'Story rules' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Story rules' })).toBeVisible()

  await page.getByRole('button', { name: /New rule|Write your first rule/ }).first().click()
  const dialog = page.getByRole('dialog', { name: 'New story rule' })
  await dialog.getByLabel(/^The rule, in your own words/).fill('Nobody but ghosts is ever both lost and found.')
  await dialog.getByRole('button', { name: 'Build it by hand' }).click()
  await dialog.getByLabel('Which entries the rule is about').selectOption('type:Character')
  await dialog.getByLabel('Must or must not').selectOption('not_both')
  await dialog.getByLabel('It cannot have this: kind of fact').selectOption('LOCATED_AT')
  await dialog.getByLabel('Together with this: kind of fact').selectOption('MEMBER_OF')
  await dialog.getByLabel('except some entries…').check()
  await dialog.getByLabel('Leave out').selectOption(wren.id)
  await expect(dialog.getByRole('list', { name: 'Left out' })).toContainText('Wren Ashdown')
  await dialog.getByLabel('and anything tagged').fill('ghost')
  await dialog.getByLabel('It must hold').selectOption('every_moment')

  const how = dialog.getByRole('region', { name: 'How it will be checked' })
  await expect(how).toContainText(
    'Checked as: At every moment of the story: Every character, except for Wren Ashdown and anything tagged “ghost”, ' +
      'cannot have both this: it is located at something and this: it is a member of something.',
  )
  await dialog.getByLabel('A short name for it').fill('Never both, at once')
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Keep this rule' }).click()

  const card = page.getByRole('listitem').filter({ hasText: 'Never both, at once' })
  await expect(card).toContainText('Checked as: At every moment of the story')
  await expect(card).toContainText(/Holds|Broken now|May be broken/)
  await expectAccessible(page)

  // Clean up so the shared project is as other tests expect it.
  await card.getByRole('button', { name: 'Remove Never both, at once' }).click()
  await page.getByRole('button', { name: 'Remove the rule' }).click()
  await expect(page.getByRole('heading', { name: 'Never both, at once' })).toHaveCount(0)
})
