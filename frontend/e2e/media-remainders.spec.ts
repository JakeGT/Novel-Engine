import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// Batch 9 C in a real browser against a real backend with no model: a picture applies until a
// story time and keeps showing one version while a newer one is added in two checked steps
// (AST-001, AST-002); the ready views' screen says how far each view is (QRY-001); a pasted text
// can be read as the writing of a scene (EXT-001). Names are unique: the story is shared.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/media-${name}.png`, fullPage })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(
    results.violations.map(
      (violation) => `${violation.id}: ${violation.help} [${violation.nodes.map((node) => node.target.join(' ')).join(' | ')}]`,
    ),
  ).toEqual([])
}

const csrf = async (page: Page) =>
  (await page.context().cookies()).find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''

async function call(page: Page, method: string, path: string, data?: unknown) {
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': await csrf(page), 'Content-Type': 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

function chunk(type: string, data: Buffer) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body) >>> 0)
  return Buffer.concat([length, body, crc])
}

/** A real PNG: a figure on a stair against the sea, lighter or darker. */
function png(width: number, height: number, light: number): Buffer {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const figure = Math.abs(x - width * 0.4) < width * 0.05 && y > height * 0.35 && y < height * 0.8
      const stair = y > height * 0.8 - (x / width) * height * 0.3
      rows.push(
        figure ? 70 : stair ? 120 + light : 40 + light + Math.round(60 * (y / height)),
        figure ? 40 : stair ? 110 + light : 70 + light,
        figure ? 50 : stair ? 90 : 120 + Math.round(40 * (1 - y / height)),
      )
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.from(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
}

test('a picture applies until a story time and keeps one version while a new one is added', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await signIn(page)
  const ines = await call(page, 'POST', '/entities', {
    type: 'Character',
    name: 'Ines Harrow',
    expected_revision: await head(page),
  })
  const added = await page.request.fetch(
    `${API}/entities/${ines.id}/pictures?expected_revision=${await head(page)}&name=${encodeURIComponent('Ines on the salt stair')}`,
    { method: 'POST', data: png(320, 220, 0), headers: { 'X-CSRF-Token': await csrf(page), 'Content-Type': 'image/png' } },
  )
  expect(added.ok(), await added.text()).toBeTruthy()
  const assetId = (await added.json()).link.asset_id as string

  await page.goto(`/projects/${PROJECT}/pictures/${assetId}`)
  await expect(page.getByRole('heading', { name: 'Ines on the salt stair' })).toBeVisible()
  await expect(page.getByText(/It holds throughout the story\./)).toBeVisible()

  // A new version, sent in two checked steps.
  await page.getByLabel('Choose a new version of “Ines on the salt stair”').setInputFiles({
    name: 'ines-older.png',
    mimeType: 'image/png',
    buffer: png(320, 220, 50),
  })
  await expect(page.getByText(/Added as version 2\./)).toBeVisible()
  await expect(page.getByText(/^Version 2 · PNG/)).toBeVisible()

  // Until a story time, and always the first version.
  await page.getByRole('button', { name: 'Change who knows, when and which version' }).click()
  const until = page.getByRole('group', { name: 'No longer shows them from this story time on' })
  await until.getByLabel('At a point').check()
  await until.getByRole('textbox').first().fill('20')
  await page.getByRole('combobox', { name: 'Which version it shows' }).selectOption({ label: 'Always version 1' })
  await shot(page, '1-picture-window-1280')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText(/It shows them until year 20\./)).toBeVisible()
  await expect(page.getByText(/It always shows version 1\./)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '2-picture-saved-1280')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.reload()
  await expect(page.getByText(/It shows them until year 20\./)).toBeVisible()
  await shot(page, '3-picture-390-dark')
  await page.emulateMedia({ colorScheme: 'light' })
})

test('the ready views say how far each is, and a pasted text can be read as a scene', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await signIn(page)
  await page.goto(`/projects/${PROJECT}/timeline`)
  await page.getByRole('link', { name: 'See how up to date the ready views are' }).click()
  await expect(page.getByRole('heading', { name: 'Ready views of the story' })).toBeVisible()
  await expect(page.getByText(/For the main story\./)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Where everyone is' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What leans on what' })).toBeVisible()
  await page.getByRole('button', { name: 'Check them against the history now' }).click()
  await expect(page.getByText(/Checked 6 views: 6 matched\./)).toBeVisible()
  await expect(page.getByText(/All of them are ready\./)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '4-views-1280')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.reload()
  await expect(page.getByText(/All of them are ready\./)).toBeVisible()
  await shot(page, '5-views-390-dark')
  await page.emulateMedia({ colorScheme: 'light' })

  await page.setViewportSize({ width: 1280, height: 900 })
  await call(page, 'POST', '/plan', { kind: 'scene', title: 'The Salt Stair', expected_revision: await head(page) })
  const text = await call(page, 'POST', '/texts', {
    title: 'Ines climbs',
    kind: 'paste',
    content: 'Ines Harrow climbed the salt stair and wondered who had left the gate open.',
  })
  await page.goto(`/projects/${PROJECT}/texts/${text.id}`)
  const choice = page.getByRole('combobox', { name: /Is this the writing of a scene in your plan\?/ })
  await expect(choice).toBeVisible()
  await choice.selectOption({ label: 'The Salt Stair' })
  await expect(page.getByText(/the assistant also suggests which of the plan’s threads it moves along/)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '6-text-as-scene-1280')
})
