import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The full Phase 2 vertical workflow in a real browser against a real backend:
// open a brand-new project (set up automatically), add an entry, recover from a
// clash with someone else's change, inspect its activity, review and commit a
// model-origin proposal, archive with an explicit acknowledgement, and export
// the project's history.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  return { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' }
}

async function post(page: Page, path: string, data: unknown) {
  return page.request.post(`${API}${path}`, { data, headers: await csrfHeaders(page) })
}

async function expectAccessible(page: Page) {
  // Dialogs fade in; measure contrast on the settled page, not mid-animation.
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  // Name the offending elements so a failure says where to look.
  expect(
    results.violations.map(
      (violation) =>
        `${violation.id}: ${violation.help} [${violation.nodes.map((node) => node.target.join(' ')).join(' | ')}]`,
    ),
  ).toEqual([])
}

test('author, review, archive, and export a project end to end', async ({ page }) => {
  // --- the public front page, sign in (local mode), open the project ------------
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Your story’s memory')
  await expectAccessible(page)
  await page.getByRole('link', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: /Your project/ }).click()

  // --- a new project is ready to use without any setup step ----------------------
  await expect(page.getByRole('heading', { level: 1 })).toContainText('build your story bible')
  await expect(page.getByText('Version 0 · saved')).toBeVisible()
  await expect
    .poll(async () => {
      const versions = await (await page.request.get(`${API}/schemas`)).json()
      return versions.map((version: { status: string }) => version.status)
    })
    .toEqual(['published'])
  await expectAccessible(page)

  // --- add an entry ---------------------------------------------------------------
  // The assistant leads the dialog; this run has no AI provider, so fill it in by hand.
  await page.getByRole('button', { name: /Add a character/ }).click()
  await expect(page.getByRole('form', { name: 'Describe it' }).getByRole('radio', { name: 'Character' })).toBeChecked()
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Fill it in by hand' }).click()
  const create = page.getByRole('form', { name: 'New entry' })
  await expect(create.getByRole('radio', { name: 'Character' })).toBeChecked()
  await create.getByLabel('Name', { exact: true }).fill('Mara Ven')
  await create.getByLabel('Also known as').fill('The Fox')
  await create.getByRole('button', { name: 'Add a detail' }).click()
  await create.getByLabel('Detail 1 name').fill('role')
  await create.getByLabel('Detail 1 value').fill('harbor pilot')
  await expectAccessible(page)
  await create.getByRole('button', { name: 'Add to story bible' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Mara Ven' })).toBeVisible()
  await expect(page.getByText('Version 1 · saved')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Details' })).toContainText('harbor pilot')
  await expectAccessible(page)

  // --- edit it, recovering from a clash without losing the entered data -----------
  await page.getByRole('button', { name: 'Edit' }).first().click()
  const edit = page.getByRole('form', { name: 'Edit Mara Ven' })
  await edit.getByLabel('Name', { exact: true }).fill('Mara Venn')

  // Someone else commits while the form is open: the head moves to version 2.
  const other = await post(page, '/entities', {
    type: 'Location',
    name: 'Saltkeep',
    expected_revision: 1,
  })
  expect(other.status()).toBe(201)

  await edit.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Someone else changed the story while you were editing.')).toBeVisible()
  await expect(edit.getByLabel('Name', { exact: true })).toHaveValue('Mara Venn')
  await page.getByRole('button', { name: 'Save on top of the latest version' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Mara Venn' })).toBeVisible()
  await expect(page.getByText('Version 3 · saved')).toBeVisible()

  // --- a fact, built as a sentence --------------------------------------------------
  const facts = page.getByRole('region', { name: 'Facts' })
  await facts.getByLabel('How the sentence continues').selectOption('LOCATED_AT')
  await facts.getByLabel('What Mara Venn is located at').selectOption({ label: 'Saltkeep (Location)' })
  await facts.getByRole('button', { name: 'Add fact' }).click()
  await expect(facts.getByRole('listitem').first()).toContainText('Mara Venn is located at Saltkeep')
  await expect(page.getByText('Version 4 · saved')).toBeVisible()

  // --- activity explains every change -----------------------------------------------
  const activity = page.getByRole('region', { name: 'Activity' })
  await expect(activity.getByText('Edited')).toBeVisible()
  await expect(activity.getByText('Added')).toBeVisible()
  await expect(activity.getByText(/by you/).first()).toBeVisible()
  await expectAccessible(page)

  // --- a model-origin proposal arrives and is reviewed in the UI -----------------
  const proposed = await post(page, '/mutations', {
    operations: [
      { op: 'create_entity', id: 'tmp:harbor', type: 'Location', name: 'Lantern Harbor' },
      { op: 'create_entity', id: 'tmp:extra', type: 'Character', name: 'Unwanted Extra' },
    ],
    expected_revision: 4,
    origin: 'model',
  })
  expect(proposed.status()).toBe(201)
  const mutationId = (await proposed.json()).id as string
  // It cannot be committed without a human decision.
  const blocked = await post(page, `/mutations/${mutationId}/commit`, {})
  expect(blocked.status()).toBe(409)
  expect((await blocked.json()).code).toBe('review_required')

  await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: /Review/ }).click()
  await page.getByRole('link', { name: 'Changes from the AI assistant' }).click()
  await expect(page.getByText('All 2 changes will be added')).toBeVisible()
  await expect(page.getByText('Add location “Lantern Harbor”')).toBeVisible()
  await expectAccessible(page)

  await page
    .getByRole('group', { name: 'Change 2: Add character “Unwanted Extra”' })
    .getByRole('button', { name: 'Skip' })
    .click()
  await expect(page.getByText('1 of 2 changes will be added')).toBeVisible()
  await page.getByRole('button', { name: 'Check for problems' }).click()
  await expect(page.getByText('No problems found.')).toBeVisible()
  await page.getByRole('button', { name: 'Approve and add to canon' }).click()
  await expect(page.getByText('Added to canon as version 5.')).toBeVisible()

  // --- only the accepted change became canon ----------------------------------------
  await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: /Story bible/ }).click()
  await expect(page.getByRole('link', { name: /Lantern Harbor/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Unwanted Extra/ })).toHaveCount(0)
  await expectAccessible(page)

  // --- archive with an explicit acknowledgement --------------------------------------
  await page.getByRole('link', { name: /Lantern Harbor/ }).click()
  await page.getByRole('button', { name: 'Archive' }).click()
  await expect(page.getByRole('button', { name: 'Archive entry' })).toBeDisabled()
  await expectAccessible(page)
  await page.getByLabel('I understand this is a significant change').check()
  await page.getByRole('button', { name: 'Archive entry' }).click()
  await expect(page.getByText('This entry is archived.')).toBeVisible()
  await expect(page.getByText('Version 6 · saved')).toBeVisible()

  // --- history explains every version, newest first -----------------------------------
  await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: 'History' }).click()
  const versions = page.getByRole('main').getByRole('listitem')
  await expect(versions).toHaveCount(6)
  await expect(versions.first()).toContainText('Version 6')
  await expect(versions.filter({ hasText: 'Version 5' })).toContainText('the AI assistant')
  await expectAccessible(page)

  // --- types and fields, in dark mode too -----------------------------------------------
  await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: 'Types and fields' }).click()
  await expect(page.getByRole('region', { name: 'Kinds of fact' })).toBeVisible()
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  // Let the colour transitions finish so contrast is measured on the final colours.
  await page.waitForTimeout(400)
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Switch to light theme' }).click()

  // --- export: the archive carries the whole accepted history ---------------------
  const exported = await post(page, '/archive/export', {})
  expect(exported.status()).toBe(200)
  const archive = await exported.json()
  expect(archive.format).toBe('novel-engine/canon-archive')
  expect(archive.commits.map((commit: { revision: number }) => commit.revision)).toEqual([
    1, 2, 3, 4, 5, 6,
  ])
  expect(archive.project.head_revision).toBe(6)
  const names = archive.canon.entities.map((entity: { name: string }) => entity.name).sort()
  expect(names).toEqual(['Lantern Harbor', 'Mara Venn', 'Saltkeep'])
  expect(JSON.stringify(archive)).not.toContain('Unwanted Extra')
  const origins = archive.commits.map((commit: { origin: string }) => commit.origin)
  expect(origins).toEqual(['author', 'author', 'author', 'author', 'model', 'author'])
})

// Keyboard-only: every step below uses Tab/Shift+Tab, typing, Enter, and Space.
// No pointer events are sent, so an unreachable or unlabelled control fails the test.
test('the core authoring path works with the keyboard alone', async ({ page }) => {
  /** Press Tab until the focused element has the wanted accessible name. */
  async function tabTo(name: RegExp | string, limit = 60): Promise<void> {
    for (let presses = 0; presses < limit; presses += 1) {
      await page.keyboard.press('Tab')
      // `document.activeElement` always exists (the body when nothing is focused).
      const accessible = await page.evaluate(() => {
        const element = document.activeElement
        if (!element || element === document.body) return ''
        const control = element as HTMLInputElement
        const label =
          element.getAttribute('aria-label') ??
          control.labels?.[0]?.textContent ??
          element.textContent ??
          ''
        return label.trim()
      })
      if (typeof name === 'string' ? accessible === name : name.test(accessible)) return
    }
    throw new Error(`could not reach ${String(name)} with the keyboard`)
  }

  /** A focused control must show a visible focus indicator. */
  async function expectVisibleFocus(): Promise<void> {
    const outline = await page.evaluate(() => {
      const style = getComputedStyle(document.activeElement as Element)
      return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) }
    })
    expect(outline.style).not.toBe('none')
    expect(outline.width).toBeGreaterThanOrEqual(2)
  }

  await page.goto('/')
  // The previous test signed in on its own context; sign in again by keyboard.
  await tabTo('Sign in')
  await expectVisibleFocus()
  await page.keyboard.press('Enter')
  // Signing in navigates away and back; wait for the library before tabbing on.
  await expect(page.getByRole('link', { name: /Your project/ })).toBeVisible()
  await tabTo(/Your project/)
  await expectVisibleFocus()
  await page.keyboard.press('Enter')
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  // Open the new-entry dialog; focus lands in it, and Tab stays inside the dialog.
  await tabTo('New entry')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Describe it in your own words')).toBeFocused()
  await expectVisibleFocus()
  await tabTo('Fill it in by hand')
  await page.keyboard.press('Enter')
  const form = page.getByRole('form', { name: 'New entry' })
  const name = form.getByLabel('Name', { exact: true })
  await expect(name).toBeFocused()
  await expectVisibleFocus()

  // Submitting an empty form by keyboard reports the problem on the field itself.
  await page.keyboard.press('Enter')
  await expect(name).toHaveAttribute('aria-invalid', 'true')
  const describedBy = await name.getAttribute('aria-describedby')
  expect(describedBy).toBeTruthy()
  await expect(page.locator(`#${describedBy!.split(' ')[0]}`)).toBeVisible()

  // Fill the form and add the entry without touching the pointer.
  await page.keyboard.type('Keyboard Kestrel')
  await tabTo('Add to story bible')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Keyboard Kestrel' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Archive it: checkbox by Space, confirmation by Enter.
  await tabTo('Archive')
  await page.keyboard.press('Enter')
  await tabTo(/I understand this is a significant change/)
  await page.keyboard.press('Space')
  await tabTo('Archive entry')
  await expectVisibleFocus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('This entry is archived.')).toBeVisible()

  // The search palette opens, finds, and navigates from the keyboard.
  await page.keyboard.press('Control+k')
  await page.keyboard.type('Mara')
  await expect(page.getByRole('option').first()).toContainText('Mara Venn')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Mara Venn' })).toBeVisible()
})
