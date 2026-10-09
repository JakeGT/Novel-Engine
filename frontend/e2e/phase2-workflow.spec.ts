import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The full Phase 2 vertical workflow in a real browser against a real backend:
// publish a schema, author an entity, recover from a stale head, inspect
// provenance, review and commit a model-origin proposal, retire with an explicit
// acknowledgement, and export the project's history.

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
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([])
}

test('author, review, retire, and export a project end to end', async ({ page }) => {
  // --- sign in (local mode) and open the project -----------------------------
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Open project' }).click()
  await expect(page.getByText('Canon head')).toContainText('r0')
  await expect(page.getByText('Your role: owner')).toBeVisible()

  // --- publish the project schema ---------------------------------------------
  await page.getByRole('link', { name: 'Schema' }).click()
  await expect(page.getByText('No schema version is published yet')).toBeVisible()
  await page.getByRole('button', { name: 'Validate' }).click()
  await expect(page.getByText('The definition is valid.')).toBeVisible()
  await page.getByRole('button', { name: 'Save as draft' }).click()
  await expect(page.getByText(/Saved as draft v1/)).toBeVisible()
  await page.getByRole('button', { name: 'Publish v1' }).click()
  await expect(page.getByText('Published', { exact: true })).toBeVisible()
  await expectAccessible(page)

  // --- create an entity --------------------------------------------------------
  await page.getByRole('link', { name: 'Entities' }).click()
  const create = page.getByRole('form', { name: 'Create entity' })
  await create.getByLabel('Name', { exact: true }).fill('Mara Ven')
  await create.getByLabel('Aliases').fill('The Fox')
  await create.getByRole('button', { name: 'Add attribute' }).click()
  await create.getByLabel('Attribute 1 name').fill('role')
  await create.getByLabel('Attribute 1 value').fill('harbor pilot')
  await create.getByRole('button', { name: 'Create entity' }).click()
  await expect(page.getByRole('link', { name: 'Mara Ven' })).toBeVisible()
  await expect(page.getByText('Canon head')).toContainText('r1')
  await expectAccessible(page)

  // --- edit it, recovering from a stale head without losing the entered data ---
  await page.getByRole('link', { name: 'Mara Ven' }).click()
  await expect(page.getByRole('heading', { name: /Mara Ven/ })).toBeVisible()
  await page.getByRole('button', { name: 'Edit' }).click()
  const edit = page.getByRole('form', { name: 'Edit entity' })
  await edit.getByLabel('Name', { exact: true }).fill('Mara Venn')

  // Someone else commits while the form is open: the head moves to r2.
  const other = await post(page, '/entities', {
    type: 'Location',
    name: 'Saltkeep',
    expected_revision: 1,
  })
  expect(other.status()).toBe(201)

  await edit.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Canon changed while you were editing.')).toBeVisible()
  await expect(edit.getByLabel('Name', { exact: true })).toHaveValue('Mara Venn')
  await page.getByRole('button', { name: 'Reload head and resubmit' }).click()
  await expect(page.getByRole('heading', { name: /Mara Venn/ })).toBeVisible()
  await expect(page.getByText('Canon head')).toContainText('r3')

  // --- history and provenance ---------------------------------------------------
  const provenance = page.getByRole('region', { name: 'History and provenance' })
  await expect(provenance.getByText('update_entity')).toBeVisible()
  await expect(provenance.getByText('create_entity')).toBeVisible()
  await expect(provenance.getByText(/origin author/).first()).toBeVisible()
  await expectAccessible(page)

  // --- a model-origin proposal arrives and is reviewed in the UI -----------------
  const proposed = await post(page, '/mutations', {
    operations: [
      { op: 'create_entity', id: 'tmp:harbor', type: 'Location', name: 'Lantern Harbor' },
      { op: 'create_entity', id: 'tmp:extra', type: 'Character', name: 'Unwanted Extra' },
    ],
    expected_revision: 3,
    origin: 'model',
  })
  expect(proposed.status()).toBe(201)
  const mutationId = (await proposed.json()).id as string
  // It cannot be committed without a human decision.
  const blocked = await post(page, `/mutations/${mutationId}/commit`, {})
  expect(blocked.status()).toBe(409)
  expect((await blocked.json()).code).toBe('review_required')

  await page.getByRole('link', { name: /^Review/ }).click()
  await page.getByRole('link', { name: 'Change from model' }).click()
  await expect(page.getByText(/Not canon yet\. 2 of 2 operations/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Commit to canon' })).toBeDisabled()
  await expectAccessible(page)

  await page.getByLabel('Note for operation 2 (optional)').fill('not part of this story')
  await page.getByRole('button', { name: 'Reject operation 2' }).click()
  await expect(page.getByText('Rejected · will not be committed')).toBeVisible()
  await page.getByRole('button', { name: 'Check against canon' }).click()
  await expect(page.getByText('No blocking problems found.')).toBeVisible()
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByRole('heading', { name: /Proposal/ })).toContainText(
    'Approved · not yet canon',
  )
  await page.getByRole('button', { name: 'Commit to canon' }).click()
  await expect(page.getByText('Committed to canon as revision r4.')).toBeVisible()

  // --- only the accepted operation became canon ----------------------------------
  await page.getByRole('link', { name: 'Entities' }).click()
  await expect(page.getByRole('link', { name: 'Lantern Harbor' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Unwanted Extra' })).toHaveCount(0)

  // --- retire with an explicit high-impact acknowledgement -----------------------
  await page.getByRole('link', { name: 'Lantern Harbor' }).click()
  await page.getByRole('button', { name: 'Retire…' }).click()
  await expect(page.getByRole('button', { name: 'Retire entity' })).toBeDisabled()
  await page.getByLabel('I understand this is a high-impact change').check()
  await page.getByRole('button', { name: 'Retire entity' }).click()
  await expect(page.getByRole('heading', { name: /Lantern Harbor/ })).toContainText('Retired')
  await expect(page.getByText('Canon head')).toContainText('r5')

  // --- the revision list explains every commit ------------------------------------
  await page.getByRole('link', { name: 'History' }).click()
  await expect(page.getByRole('row')).toHaveCount(6) // header + r1..r5
  await expect(page.getByRole('row', { name: /r4/ })).toContainText('model')
  await expectAccessible(page)

  // --- export: the archive carries the whole accepted history ---------------------
  const exported = await post(page, '/archive/export', {})
  expect(exported.status()).toBe(200)
  const archive = await exported.json()
  expect(archive.format).toBe('novel-engine/canon-archive')
  expect(archive.commits.map((commit: { revision: number }) => commit.revision)).toEqual([
    1, 2, 3, 4, 5,
  ])
  expect(archive.project.head_revision).toBe(5)
  const names = archive.canon.entities.map((entity: { name: string }) => entity.name).sort()
  expect(names).toEqual(['Lantern Harbor', 'Mara Venn', 'Saltkeep'])
  expect(JSON.stringify(archive)).not.toContain('Unwanted Extra')
  const origins = archive.commits.map((commit: { origin: string }) => commit.origin)
  expect(origins).toEqual(['author', 'author', 'author', 'model', 'author'])
})
