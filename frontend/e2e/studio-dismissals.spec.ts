import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Findings the author can act on, in a real browser against a real backend, with no AI
// provider: a check in the scene studio is dismissed with a reason, listed under Dismissed
// and brought back; a story rule is set to block, and a suggested change that breaks it is
// held back in review (CON-010, CON-015, CON-016).
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/findings-${name}.png`, fullPage })
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

test('a check is dismissed with a reason, listed under Dismissed, and brought back', async ({ page }) => {
  await signIn(page)
  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['oriel', 'Character', 'Oriel Vane'],
    ['quay', 'Location', 'Copper Quay'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Low Tide at Copper Quay',
    summary: 'Oriel waits for a boat that does not come.',
    refs: [
      { entity_id: ids.oriel, role: 'point_of_view' },
      { entity_id: ids.quay, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.fill(
    'Oriel Vane waited at Copper Quay. The silence was palpable. A wave of cold came off the water, ' +
      'and the boat did not come.',
  )
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()

  const checks = page.getByRole('region', { name: 'Checks' })
  const palpable = /^Dismiss: “palpable”/
  await checks.getByRole('button', { name: palpable }).click()
  const form = checks.getByRole('group', { name: palpable })
  await expect(form.getByRole('button', { name: 'Intended' })).toHaveAttribute('aria-pressed', 'true')
  await form.getByRole('button', { name: 'Other reason' }).click()
  await form.getByLabel('Your reason').fill('Oriel would say it this way.')
  await shot(page, '01-dismiss-form')
  await expectAccessible(page)
  await form.getByRole('button', { name: 'Dismiss', exact: true }).click()

  await expect(checks.getByText(/^Dismissed: “Oriel would say it this way.”/)).toBeVisible()
  await checks.getByText('Dismissed: 1 check').click()
  await expect(checks.getByText('“Oriel would say it this way.” · until the words change')).toBeVisible()
  await shot(page, '02-dismissed')
  await expectAccessible(page)

  // At phone width the margin moves under the text.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await checks.getByText('Dismissed: 1 check').click()
  await checks.getByText('Dismissed: 1 check').scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await shot(page, '03-dismissed-phone', false)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.reload()

  // It stays dismissed when another sentence changes.
  await editor.fill(
    'Oriel Vane waited at Copper Quay. The silence was palpable. A wave of cold came off the water, ' +
      'and still the boat did not come.',
  )
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 2/)).toBeVisible()
  await expect(checks.getByText('Dismissed: 1 check')).toBeVisible()

  await checks.getByText('Dismissed: 1 check').click()
  await checks.getByRole('button', { name: /^Undo dismissing: “palpable”/ }).click()
  await expect(checks.getByText(/Dismissed:/)).toHaveCount(0)
  await expect(checks.getByRole('button', { name: palpable })).toBeVisible()
})

test('a rule set to block holds back a suggested change that breaks it', async ({ page }) => {
  await signIn(page)
  const guild = (await call(page, 'POST', '/entities', { type: 'Faction', name: 'Ropewalk Guild', expected_revision: await head(page) })).id
  const member = (await call(page, 'POST', '/entities', { type: 'Character', name: 'Tamsin Rook', expected_revision: await head(page) })).id
  const fact = await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: member,
      predicate: 'MEMBER_OF',
      object: { kind: 'entity_ref', entity_id: guild, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  const rule = await call(page, 'POST', '/rules', {
    title: 'Guilds have members',
    sentence: 'Every guild has at least one member.',
    body: {
      applies_to: { type: 'Faction' },
      checks: [{ kind: 'at_least', count: 1, pattern: { predicate: 'MEMBER_OF', direction: 'in' } }],
    },
    examples: [],
  })

  await page.goto(`/projects/${PROJECT}/rules`)
  const choice = page.getByRole('group', { name: 'When a change would break Guilds have members' })
  await expect(choice.getByRole('button', { name: 'Warn me' })).toHaveAttribute('aria-pressed', 'true')
  await choice.getByRole('button', { name: 'Block the change' }).click()
  await expect(page.getByText('now blocks any change that would break it.')).toBeVisible()
  await expect(choice.getByRole('button', { name: 'Block the change' })).toHaveAttribute('aria-pressed', 'true')
  await shot(page, '04-rule-blocks')
  await expectAccessible(page)

  const proposal = await call(page, 'POST', '/mutations', {
    origin: 'model',
    expected_revision: await head(page),
    operations: [{ op: 'close_fact', id: fact.id }],
  })
  await call(page, 'POST', `/mutations/${proposal.id}/submit`, { note: 'test' })
  await page.goto(`/projects/${PROJECT}/review/${proposal.id}`)
  await expect(page.getByText(/This must be fixed first/)).toBeVisible()
  await expect(page.getByText(/A change breaks a story rule you set to block/)).toBeVisible()
  await expect(page.getByRole('button', { name: /Approve and add/ })).toBeDisabled()
  await shot(page, '05-review-held-back')
  await expectAccessible(page)

  // Leave the shared story as other tests expect it.
  await call(page, 'POST', `/mutations/${proposal.id}/reject`, { note: 'test' })
  await call(page, 'DELETE', `/rules/${rule.id}`)
})
