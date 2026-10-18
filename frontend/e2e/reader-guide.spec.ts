import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

// The reader's guide up to a scene (QRY-002), in a real browser against a real backend, with
// no AI provider: the guide is a page to read, print or download; it can show the story as
// it stood at an earlier version, and its heading, printed page and Markdown file all say
// which version, scene and viewpoint they show.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/reader-guide-${name}.png`, fullPage })
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

test('the reader’s guide reads, prints and downloads, now or at an earlier version', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['ida', 'Character', 'Ida Quill'],
    ['tower', 'Location', 'The Signal Tower'],
    ['harbour', 'Location', 'Cold Harbour'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const prop = (target: string) => ({
    subject_id: ids.ida,
    predicate: 'LOCATED_AT',
    object: { kind: 'entity_ref', entity_id: ids[target], project_id: PROJECT },
  })
  ids.atTower = (await call(page, 'POST', '/facts', { proposition: prop('tower'), expected_revision: await head(page) })).id
  ids.scene = (
    await call(page, 'POST', '/plan', {
      kind: 'scene',
      title: 'The Lamp Goes Out',
      refs: [
        { entity_id: ids.ida, role: 'point_of_view' },
        { entity_id: ids.tower, role: 'place' },
        { entity_id: ids.harbour, role: 'participant' },
      ],
      expected_revision: await head(page),
    })
  ).id
  const then = await head(page)
  // Afterwards Ida leaves the tower for the harbour.
  await call(page, 'POST', `/facts/${ids.atTower}/close`, { expected_revision: then })
  await call(page, 'POST', '/facts', { proposition: prop('harbour'), expected_revision: await head(page) })
  const latest = await head(page)

  await page.goto(`/projects/${PROJECT}/plan`)
  const scene = page.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'The Lamp Goes Out' }) })
  await scene.getByRole('link', { name: 'Reader’s guide up to this scene' }).click()

  const sheet = page.getByRole('article', { name: new RegExp(`after “The Lamp Goes Out” — version ${latest}`) })
  await expect(sheet).toBeVisible()
  await expect(sheet.getByText('Ida Quill is located at Cold Harbour.')).toBeVisible()
  await expect(sheet.getByText(`The story itself, as it stood at version ${latest}`, { exact: false })).toBeVisible()
  await expectAccessible(page)
  await shot(page, 'latest')

  // The story as it stood when the scene was planned: Ida was still at the tower.
  await page.getByLabel('Version of the story').selectOption(String(then))
  const old = page.getByRole('article', { name: new RegExp(`version ${then}$`) })
  await expect(old.getByText('Ida Quill is located at The Signal Tower.')).toBeVisible()
  await expect(old.getByText('located at Cold Harbour')).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`version=${then}`))
  await shot(page, 'earlier-version')

  // The file says exactly what it shows, in a header any Markdown tool reads.
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download as Markdown' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^The story so far, after The Lamp Goes Out version \d+\.md$/)
  const markdown = await readFile((await download.path()) as string, 'utf8')
  expect(markdown.startsWith('---\n')).toBeTruthy()
  expect(markdown).toContain(`\nversion: ${then}\n`)
  expect(markdown).toContain('story_line: "the story itself"')
  expect(markdown).toContain('scene: "The Lamp Goes Out"')
  expect(markdown).toContain('- Ida Quill is located at The Signal Tower.')

  // Printing shows only the guide: no menu, no controls.
  await page.emulateMedia({ media: 'print' })
  await expect(page.locator('.sidebar')).toBeHidden()
  await expect(page.getByLabel('Version of the story')).toBeHidden()
  await expect(old).toBeVisible()
  await shot(page, 'print')
  await page.emulateMedia({ media: 'screen' })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('article', { name: new RegExp(`version ${then}$`) })).toBeVisible()
  await expectAccessible(page)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await shot(page, 'phone-390')
})
