import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Batch 8 B, in a real browser against the local stand-in model: starting from a premise
// and answering in turn (AST-EXP-005), Change anything remembering the conversation and
// clearing it (AST-EXP-017), and merging two entries as one reviewed change that is undone
// in one step (AST-EXP-017). The story is shared with other specs, so everything here is
// looked up by names only this spec uses.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (!SHOTS) return
  await page.waitForTimeout(450)
  await page.screenshot({ path: `${SHOTS}/b-${name}.png`, fullPage })
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

test('a premise is read, questions are answered in turn, and suggestions wait for review', async ({ page }) => {
  await signIn(page)
  await call(page, 'DELETE', '/assist/threads/premise')

  await page.goto(`/projects/${PROJECT}/premise`)
  await expect(page.getByRole('heading', { level: 1, name: 'Start from your story' })).toBeVisible()
  await shot(page, '1-premise-empty')
  await expectAccessible(page)
  await page
    .getByLabel('Your story, in your own words')
    .fill('Isolde Varrow keeps the tolls at The Copper Weir. Her brother has not come home.')
  await page.getByRole('button', { name: 'Read it and ask me' }).click()

  const round = page.getByRole('status').filter({ hasText: 'to review' })
  await expect(round.getByRole('heading', { name: '3 suggested changes to review' })).toBeVisible()
  await expect(page.getByLabel("What is her brother's name?")).toBeVisible()
  await expect(page.getByLabel('Who owns The Copper Weir?')).toBeVisible()
  await shot(page, '2-premise-questions')
  await expectAccessible(page)

  // Answer one, skip the other; the next round suggests only what the answer adds.
  await page.getByLabel("What is her brother's name?").fill('He is called Fenwick.')
  await page.getByRole('button', { name: 'Send my answers' }).click()
  await expect(page.getByRole('heading', { name: '1 suggested change to review' })).toBeVisible()
  await expect(page.getByText('The assistant has no more questions.')).toBeVisible()
  // The conversation so far stays in view: the premise and the answer.
  const earlier = page.getByRole('region', { name: 'Earlier in this conversation' })
  await expect(earlier.getByText('He is called Fenwick.')).toBeVisible()
  await shot(page, '3-premise-answered')

  // Nothing is in the story yet: the suggestions wait in review.
  await page.getByRole('link', { name: 'Review the suggestions' }).click()
  await expect(page.getByText('Not part of your story yet.')).toBeVisible()
  await expect(page.getByText('Add character “Fenwick”')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/projects/${PROJECT}/premise`)
  await expect(page.getByRole('region', { name: 'Earlier in this conversation' })).toBeVisible()
  await shot(page, '4-premise-phone')
  await expectAccessible(page)
})

test('Change anything remembers the conversation across visits until it is cleared', async ({ page }) => {
  await signIn(page)
  await call(page, 'DELETE', '/assist/threads/change')

  await page.getByRole('button', { name: 'Change anything' }).click()
  let dialog = page.getByRole('dialog', { name: 'Change anything' })
  await dialog.getByLabel('What should change?').fill('Make the weir older.')
  await dialog.getByRole('button', { name: 'Show me what would change' }).click()
  await expect(dialog.getByText('• What should change?')).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  // A new visit: what was said before is there, and read again by the assistant.
  await page.reload()
  await page.getByRole('button', { name: 'Change anything' }).click()
  dialog = page.getByRole('dialog', { name: 'Change anything' })
  const earlier = dialog.getByRole('region', { name: 'Earlier in this conversation' })
  await expect(earlier.getByText('Make the weir older.')).toBeVisible()
  await shot(page, '5-change-remembered', false)
  await expectAccessible(page)

  await earlier.getByRole('button', { name: 'Clear and start again' }).click()
  await earlier.getByRole('button', { name: 'Clear conversation' }).click()
  await expect(dialog.getByRole('region', { name: 'Earlier in this conversation' })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

test('two entries are merged as one reviewed change and undone in one step', async ({ page }) => {
  await signIn(page)
  const ids: Record<string, string> = {}
  for (const [key, name, aliases] of [
    ['quill', 'Quill Harrowgate', []],
    ['q', 'Q. Harrowgate', ['The Toll-Keeper']],
    ['brann', 'Brann Oakes', []],
  ] as const) {
    ids[key] = (
      await call(page, 'POST', '/entities', {
        type: 'Character',
        name,
        aliases,
        expected_revision: await head(page),
      })
    ).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.brann,
      predicate: 'KNOWS',
      object: { kind: 'entity_ref', entity_id: ids.q, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })

  await page.goto(`/projects/${PROJECT}/entries/${ids.q}`)
  await page.getByRole('button', { name: 'Merge with another entry' }).click()
  const dialog = page.getByRole('dialog', { name: 'Merge “Q. Harrowgate” with another entry' })
  await dialog.getByLabel('Find a character by name').fill('Harrowgate')
  await dialog.getByRole('radio', { name: 'Quill Harrowgate' }).check()
  await dialog.getByRole('radio', { name: 'Keep “Quill Harrowgate” and archive “Q. Harrowgate”' }).check()
  await expect(dialog.getByText('Brann Oakes knows Q. Harrowgate')).toBeVisible()
  await shot(page, '6-merge-preview', false)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Send merge to review' }).click()

  await expect(page.getByText('Merge “Q. Harrowgate” into “Quill Harrowgate”')).toBeVisible()
  await expect(page.getByText('Brann Oakes knows Q. Harrowgate')).toBeVisible()
  await shot(page, '7-merge-review')
  await expectAccessible(page)
  const mutationId = page.url().split('/').pop() as string
  await call(page, 'POST', `/mutations/${mutationId}/approve`, {})
  const saved = await call(page, 'POST', `/mutations/${mutationId}/commit`)

  await page.goto(`/projects/${PROJECT}/entries/${ids.quill}`)
  await expect(page.getByText(/also known as Q\. Harrowgate, The Toll-Keeper/)).toBeVisible()
  await page.goto(`/projects/${PROJECT}/entries/${ids.q}`)
  await expect(page.getByText('This entry is archived.')).toBeVisible()

  // Undo: one step brings it all back.
  const preview = await call(page, 'POST', '/reverts/preview', { from_revision: saved.revision - 1 })
  expect(preview.conflicts).toEqual([])
  await call(page, 'POST', '/reverts', {
    from_revision: saved.revision - 1,
    expected_revision: saved.revision,
    acknowledge_high_impact: true,
  })
  await page.reload()
  await expect(page.getByRole('button', { name: 'Merge with another entry' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Merge with another entry' }).click()
  await page.getByRole('radio', { name: 'Quill Harrowgate' }).check()
  await expect(page.getByText('Brann Oakes knows Q. Harrowgate')).toBeVisible()
  await shot(page, '8-merge-phone')
  // The same dialog on a desk at night.
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: 'Merge with another entry' }).click()
  await page.getByRole('radio', { name: 'Quill Harrowgate' }).check()
  await expect(page.getByText('Brann Oakes knows Q. Harrowgate')).toBeVisible()
  await shot(page, '9-merge-dark', false)
  await expectAccessible(page)
})
