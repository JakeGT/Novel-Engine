import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// The media page (AST-007, AST-008) and the parts of the reader's guide (QRY-002), in a real
// browser against a real backend with no model: a picture is added from the page and shown
// on a scene, found again by words, and once it is shown nowhere its file is deleted after a
// deliberate tick; the guide is made as a timeline and as a recap.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/C-${name}.png`, fullPage })
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

/** A real PNG: a lamp-lit tower against a dusk sky, so screenshots show a picture. */
function png(width: number, height: number): Buffer {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const tower = Math.abs(x - width / 2) < width * 0.06 && y > height * 0.3
      const t = y / height
      rows.push(
        tower ? 60 : 90 + Math.round(120 * t),
        tower ? 50 : 70 + Math.round(60 * t),
        tower ? 40 : 140 - Math.round(60 * t),
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

test('a picture is added and found from the media page, then its file deleted', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  await call(page, 'POST', '/entities', { type: 'Character', name: 'Wren Hollis', expected_revision: await head(page) })
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'The Lamp Room',
    expected_revision: await head(page),
  })

  await page.goto(`/projects/${PROJECT}/media`)
  await expect(page.getByRole('heading', { name: 'Add a picture' })).toBeVisible()
  await page.getByLabel('Show it on').selectOption(`scene:${scene.id}`)
  await page.getByLabel('Picture file').setInputFiles({
    name: 'lighthouse-at-dusk.png',
    mimeType: 'image/png',
    buffer: png(320, 220),
  })
  await expect(page.getByLabel('Name')).toHaveValue('lighthouse-at-dusk')
  await page.getByLabel('Name').fill('Lighthouse at dusk')
  await shot(page, '1-media-add-1280')
  await page.getByRole('button', { name: 'Add picture' }).click()
  await expect(page.getByText(/Added “Lighthouse at dusk” to The Lamp Room/)).toBeVisible()
  await expectAccessible(page)

  await page.getByLabel('Words to look for').fill('lighthouse dusk')
  await page.getByRole('button', { name: 'Find' }).click()
  const found = page.locator('.media-found')
  await expect(found.getByRole('link', { name: 'Lighthouse at dusk' })).toBeVisible()
  await expect(found.getByText('Found by its name')).toBeVisible()
  await shot(page, '2-media-find-1280')
  await expectAccessible(page)

  // Shown on a scene, its file cannot be deleted: the page says why.
  const item = page.locator('li.card', { hasText: 'Lighthouse at dusk' })
  await item.getByRole('button', { name: /Lighthouse at dusk/ }).click()
  await expect(item.getByText(/shown on one entry or scene. Take it off them first/)).toBeVisible()
  await expect(item.getByRole('button', { name: 'Delete the file' })).toBeDisabled()

  // Taken off the scene (an ordinary change), it can.
  const links = await call(page, 'GET', `/links?target_id=${scene.id}`)
  const link = links.items.find((entry: { retired_revision: number | null }) => entry.retired_revision == null)
  await call(page, 'POST', `/links/${link.id}/retire`, { expected_revision: await head(page) })
  await page.reload()
  const again = page.locator('li.card', { hasText: 'Lighthouse at dusk' })
  await again.getByRole('button', { name: /Lighthouse at dusk/ }).click()
  await again.getByRole('button', { name: 'Delete the file' }).click()
  const dialog = page.getByRole('dialog', { name: 'Delete the file of “Lighthouse at dusk”?' })
  await expect(dialog.getByRole('button', { name: 'Keep the picture' })).toBeFocused()
  await expect(dialog.getByRole('button', { name: 'Delete the file for good' })).toBeDisabled()
  await dialog.getByLabel('I understand the file cannot be brought back.').check()
  await shot(page, '3-media-delete-dialog-1280', false)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Delete the file for good' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('li.card', { hasText: 'Lighthouse at dusk' }).getByText('Deleted', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(400)
  await shot(page, '4-media-dark-1280')
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Switch to light' }).click()

  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Find pictures' })).toBeVisible()
  await shot(page, '5-media-390')
  await expectAccessible(page)
})

test('the reader’s guide is made as a timeline and as a recap', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const keeper = (
    await call(page, 'POST', '/entities', { type: 'Character', name: 'Ansel Moor', expected_revision: await head(page) })
  ).id
  const rock = (
    await call(page, 'POST', '/entities', { type: 'Location', name: 'Heron Skerry', expected_revision: await head(page) })
  ).id
  const first = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Night on Heron Skerry',
    refs: [
      { entity_id: keeper, role: 'point_of_view' },
      { entity_id: rock, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  const second = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'The first winter',
    refs: [{ entity_id: keeper, role: 'point_of_view' }],
    expected_revision: await head(page),
  })
  for (const [item, at] of [
    [first, 12],
    [second, 4],
  ] as const) {
    await call(page, 'PUT', `/plan/${item.id}/when`, {
      when: { kind: 'at', at },
      expected_revision: await head(page),
    })
  }

  await page.goto(`/projects/${PROJECT}/plan/${second.id}/guide?part=timeline`)
  const sheet = page.getByRole('article', { name: /Timeline so far, after “The first winter”/ })
  await expect(sheet).toBeVisible()
  const entries = sheet.locator('.guide-entry h4')
  await expect(entries.filter({ hasText: 'The first winter' })).toBeVisible()
  // In story time, the second scene (year 4) comes before the first (year 12).
  const names = await entries.allTextContents()
  // Other tests share this story, so scenes are found by title, not by number.
  const at = (title: string) => names.findIndex((name) => name.endsWith(`: ${title}`))
  expect(at('Night on Heron Skerry')).toBeGreaterThan(-1)
  expect(at('The first winter')).toBeLessThan(at('Night on Heron Skerry'))
  await shot(page, '6-guide-timeline-1280')
  await expectAccessible(page)

  await page.getByLabel('What to include').selectOption('recap')
  const recap = page.getByRole('article', { name: /Recap so far/ })
  await expect(recap.getByText('Where: Heron Skerry.')).toBeVisible()
  // Other tests share this story: look in this test's own scene.
  await expect(
    recap
      .locator('.guide-entry')
      .filter({ hasText: 'The first winter' })
      .getByText('Set earlier in story time than the scene before it.'),
  ).toBeVisible()
  await shot(page, '7-guide-recap-1280')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('article', { name: /Recap so far/ })).toBeVisible()
  await shot(page, '8-guide-recap-390')
  await expectAccessible(page)
})
