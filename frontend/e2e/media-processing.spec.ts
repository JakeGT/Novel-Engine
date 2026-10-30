import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// Pictures are prepared on the server (AST-003): a separate, limited process makes a small
// copy for lists and a larger one for the media page. Lists show the small copy, an opened
// picture is the file itself, and a picture that could not be prepared says why in plain
// words with a way to try again. Runs against the real decoder (Pillow) with no model.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = false) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/areaA-${name}.png`, fullPage })
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

function header(width: number, height: number) {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  return ihdr
}

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** A real PNG: a sky over a sea, so the screenshots show a picture, not a blank. */
function png(width: number, height: number): Buffer {
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const sea = y > height * 0.62
      const t = y / height
      rows.push(
        sea ? 30 + Math.round(40 * t) : 210 - Math.round(90 * t),
        sea ? 70 + Math.round(30 * (x / width)) : 150 - Math.round(40 * t),
        sea ? 110 : 200 - Math.round(30 * t),
      )
    }
  }
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', header(width, height)),
    chunk('IDAT', deflateSync(Buffer.from(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** A PNG whose layout is sound but whose pixels are not: it is stored, then cannot be prepared. */
function brokenPng(): Buffer {
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', header(320, 200)),
    chunk('IDAT', Buffer.from('these bytes are not compressed pixels at all')),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

async function upload(page: Page, entityId: string, name: string, data: Buffer) {
  const response = await page.request.fetch(
    `${API}/entities/${entityId}/pictures?expected_revision=${await head(page)}&name=${encodeURIComponent(name)}`,
    { method: 'POST', data, headers: { 'X-CSRF-Token': await csrf(page), 'Content-Type': 'image/png' } },
  )
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()) as { link: { asset_id: string }; version: { id: string } }
}

async function settled(page: Page, picture: { link: { asset_id: string }; version: { id: string } }) {
  const path = `/assets/${picture.link.asset_id}/versions/${picture.version.id}/processing`
  await expect
    .poll(async () => (await call(page, 'GET', path)).status, { timeout: 30_000 })
    .toMatch(/ready|failed/)
  return (await call(page, 'GET', path)).status as string
}

test('pictures are shown small in lists, open in full, and a failed one can be tried again', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const harbour = (
    await call(page, 'POST', '/entities', { type: 'Location', name: 'Saltmarsh Harbour', expected_revision: await head(page) })
  ).id
  const good = await upload(page, harbour, 'Harbour at first light', png(1600, 1000))
  const bad = await upload(page, harbour, 'Damaged harbour sketch', brokenPng())
  expect(await settled(page, good)).toBe('ready')
  expect(await settled(page, bad)).toBe('failed')

  // The small copy is what a list asks for, and the server says it sent it.
  const thumb = await page.request.get(
    `${API}/assets/${good.link.asset_id}/versions/${good.version.id}/content?size=thumbnail`,
  )
  expect(thumb.headers()['x-picture-size']).toBe('thumbnail')
  expect(thumb.headers()['content-type']).toBe('image/webp')
  expect((await thumb.body()).length).toBeLessThan(png(1600, 1000).length)

  // The entry shows the small copies; opening one shows the picture itself.
  await page.goto(`/projects/${PROJECT}/entries/${harbour}`)
  const panel = page.getByRole('region', { name: 'Pictures' })
  await expect(panel.getByRole('img', { name: /Picture 1 of Saltmarsh Harbour/ })).toBeVisible()
  await shot(page, '1-entry-thumbnails')
  await panel.getByRole('button', { name: 'Open Picture 2 of Saltmarsh Harbour' }).click()
  const viewer = page.getByRole('dialog', { name: 'Picture 2 of Saltmarsh Harbour' })
  await expect(viewer.getByRole('img')).toBeVisible()
  await page.waitForTimeout(400)
  await shot(page, '2-picture-opened')
  await expectAccessible(page)
  await viewer.getByRole('button', { name: 'Close' }).click()
  await expect(viewer).toBeHidden()

  // The media page: each picture small, the failed one marked in words.
  await page.goto(`/projects/${PROJECT}/media`)
  const goodRow = page.getByRole('button', { name: /^Harbour at first light/ })
  const badRow = page.getByRole('button', { name: /^Damaged harbour sketch/ })
  await expect(badRow.getByText('Not ready')).toBeVisible()
  await goodRow.click()
  await expect(page.getByText(/1600 × 1000 pixels/)).toBeVisible()
  await shot(page, '3-media-ready', true)
  await goodRow.click()

  await badRow.click()
  const notice = page.getByText('No small copy for lists.')
  await expect(notice).toBeVisible()
  await expect(page.getByText(/could not be read; the file may be damaged/)).toBeVisible()
  await expect(page.getByText(/The file you added is kept as it is/)).toBeVisible()
  await shot(page, '4-media-failed', true)
  await expectAccessible(page)

  await page.getByRole('button', { name: 'Try again' }).click()
  // It is queued again at once, then fails again for the same reason (the file is broken).
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible({ timeout: 30_000 })
  await expect(notice).toBeVisible()

  // Narrow screens, light and dark.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await page.getByRole('button', { name: /^Damaged harbour sketch/ }).click()
  await page.getByText('No small copy for lists.').scrollIntoViewIfNeeded()
  await shot(page, '5-media-failed-390')
  await page.getByRole('button', { name: /^Damaged harbour sketch/ }).click()
  await page.getByRole('button', { name: /^Harbour at first light/ }).click()
  await page.getByText(/1600 × 1000 pixels/).scrollIntoViewIfNeeded()
  await shot(page, '6-media-ready-390')

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await page.evaluate(() => window.scrollTo(0, 0))
  await expect(page.getByText(/1600 × 1000 pixels/)).toBeVisible()
  await shot(page, '7-media-dark-1280')
  await expectAccessible(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await page.getByRole('button', { name: /^Harbour at first light/ }).click()
  await page.getByText(/1600 × 1000 pixels/).scrollIntoViewIfNeeded()
  await shot(page, '8-media-dark-390')
  // Leave the shared local story in light mode for the tests after this one.
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to light' }).click()
})
