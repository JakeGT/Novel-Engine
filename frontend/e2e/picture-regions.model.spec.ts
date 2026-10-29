import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// Marked parts of a picture (AST-007, AST-003, AST-005, CON-014) in a real browser against
// a real backend, with the local stand-in model (scripts/e2e/fake_model.py): draw a part
// on the picture or type where it is, name it and say who it shows; describe the picture,
// so the part gets a caption and the writing in the picture is read for review; then the
// pictures check in the studio points at the part, drawn on the picture beside the note.
// Names are this spec's own: the story is shared with every other spec.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/areaB-${name}.png`, fullPage })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
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

/** A real PNG: a doorway in a lit wall, with a dark sign board above it. */
function png(width: number, height: number): Buffer {
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const sign = x > width * 0.3 && x < width * 0.7 && y > height * 0.08 && y < height * 0.22
      const door = x > width * 0.38 && x < width * 0.62 && y > height * 0.35
      rows.push(sign ? 40 : door ? 70 : 200, sign ? 30 : door ? 45 : 170, sign ? 25 : door ? 30 : 120)
    }
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.from(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

test('mark a part of a picture, have it described and read, and see a note point at it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const bryony = (
    await call(page, 'POST', '/entities', { type: 'Character', name: 'Bryony Tull', expected_revision: await head(page) })
  ).id
  const uploaded = await page.request.fetch(
    `${API}/entities/${bryony}/pictures?expected_revision=${await head(page)}&name=${encodeURIComponent('Bryony at the inn door')}`,
    { method: 'POST', data: png(800, 600), headers: { 'X-CSRF-Token': await csrf(page), 'Content-Type': 'image/png' } },
  )
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy()
  const assetId = (await uploaded.json()).link.asset_id as string
  // Parts are marked on a prepared picture.
  await expect
    .poll(async () => (await call(page, 'GET', `/pictures/${assetId}`)).width, { timeout: 30_000 })
    .toBe(800)

  await page.goto(`/projects/${PROJECT}/pictures/${assetId}`)
  await expect(page.getByRole('heading', { name: 'Bryony at the inn door' })).toBeVisible()
  await expect(page.getByText('No parts are marked yet.')).toBeVisible()
  await page.getByRole('button', { name: 'Mark a part' }).click()
  const form = page.getByRole('form', { name: 'Mark a part' })
  await expect(form.getByText('The picture is 800 by 600 pixels.', { exact: false })).toBeVisible()

  // Drawn with the pointer: press, drag, let go.
  const picture = page.getByRole('img', { name: 'Bryony at the inn door' })
  const frame = (await picture.boundingBox())!
  await page.mouse.move(frame.x + frame.width * 0.3, frame.y + frame.height * 0.08)
  await page.mouse.down()
  await page.mouse.move(frame.x + frame.width * 0.5, frame.y + frame.height * 0.15, { steps: 4 })
  await page.mouse.move(frame.x + frame.width * 0.7, frame.y + frame.height * 0.22, { steps: 4 })
  await page.mouse.up()
  const left = Number(await form.getByLabel('From the left').inputValue())
  expect(left).toBeGreaterThanOrEqual(236)
  expect(left).toBeLessThanOrEqual(244)
  await expect(page.getByText('The part being marked is drawn on the picture.')).toBeVisible()

  // Or typed, from the keyboard alone: the same box, exactly.
  await form.getByLabel('From the left').fill('240')
  await form.getByLabel('From the top').fill('48')
  await form.getByLabel('Width').fill('320')
  await form.getByLabel('Height').focus()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type('84')
  await form.getByLabel('Name (optional)').fill('The inn sign')
  // By its id: a story shared with other runs may hold another Bryony Tull.
  await form.getByLabel('Who or what it shows (optional)').selectOption(bryony)
  await shot(page, '1-marking-a-part-1280')
  await expectAccessible(page)
  await form.getByLabel('Name (optional)').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Marked “The inn sign”.')).toBeVisible()
  const parts = page.getByRole('region', { name: 'Marked parts' })
  const sign = parts.getByRole('listitem').filter({ hasText: 'The inn sign' })
  await expect(sign.getByText(/Shows/)).toBeVisible()
  await expect(sign.getByText('320 by 84 pixels, 240 from the left and 48 from the top', { exact: false })).toBeVisible()
  const view = await call(page, 'GET', `/pictures/${assetId}`)
  expect(view.parts).toHaveLength(1)
  expect(view.parts[0]).toMatchObject({ x: 240, y: 48, width: 320, height: 84, label: 'The inn sign', entity_id: bryony })
  await sign.getByRole('button', { name: 'Show where' }).click()
  await expect(page.getByText('Marked: the part “The inn sign”.')).toBeVisible()

  // Described: the part gets its own caption, and the writing in the picture is read.
  await page.getByRole('button', { name: 'Describe this picture' }).click()
  await expect(page.getByText(/Described. 5 suggestions to review below./)).toBeVisible({ timeout: 30_000 })
  const writing = page.getByRole('listitem').filter({ hasText: 'THE BLACK SWAN' })
  await expect(writing.getByText('Writing in it')).toBeVisible()
  await expect(writing.getByText(/letter by letter/)).toBeVisible()
  const partCaption = page.getByRole('listitem').filter({ hasText: 'A close view of this part of the picture.' })
  await expect(partCaption.getByText('What a marked part shows')).toBeVisible()
  await expect(partCaption.getByText(/in the part “The inn sign”/)).toBeVisible()
  await writing.getByRole('button', { name: 'Correct' }).click()
  await page.getByLabel('Your words').fill('The Black Swan')
  await page.getByRole('button', { name: 'Save correction' }).click()
  const corrected = page.getByRole('listitem').filter({ hasText: 'The Black Swan' }).filter({ hasText: 'Writing in it' })
  await expect(corrected.getByText('Corrected by you')).toBeVisible()
  await partCaption.getByRole('button', { name: 'Accept' }).click()
  await expect(partCaption.getByText('Accepted')).toBeVisible()
  await shot(page, '2-described-and-read-1280')
  await expectAccessible(page)
  expect((await call(page, 'GET', `/pictures/${assetId}`)).description).toContain(
    'Written in the picture: “The Black Swan”',
  )

  // The pictures check, with the picture sent: the note points at the marked part.
  await page.getByRole('radio', { name: /Send the picture/ }).check()
  await expect(page.getByText('Saved: send the picture.')).toBeVisible()
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'Bryony waits at the Swan',
    refs: [{ entity_id: bryony, role: 'point_of_view' }],
    expected_revision: await head(page),
  })
  await call(page, 'PUT', `/plan/${scene.id}/studio/text`, {
    content: 'Wren Hollis crossed the yard while Bryony Tull stood under a sign painted red.',
    base_version_id: null,
  })
  await call(page, 'POST', `/plan/${scene.id}/studio/reviews`, { passes: ['pictures'] })
  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  const notes = page.getByRole('region', { name: /The assistant.s notes/ })
  const note = notes.getByRole('listitem').filter({ hasText: 'the part “The inn sign”' })
  await expect(note).toBeVisible({ timeout: 30_000 })
  await expect(note.getByRole('img', { name: /The part “The inn sign” is marked./ })).toBeVisible()
  await expect(note.getByRole('link', { name: 'Open the picture' })).toHaveAttribute(
    'href',
    `/projects/${PROJECT}/pictures/${assetId}`,
  )
  await shot(page, '3-note-points-at-the-part-1280', false)
  await expectAccessible(page)

  // Change and remove, with a way back.
  await page.goto(`/projects/${PROJECT}/pictures/${assetId}`)
  await parts.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByText(/Removed “The inn sign”./)).toBeVisible()
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('“The inn sign” is marked again.')).toBeVisible()
  await expect(parts.getByRole('listitem').filter({ hasText: 'The inn sign' })).toBeVisible()

  // Narrow, light; then dark at both widths, with the form open.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Bryony at the inn door' })).toBeVisible()
  await parts.getByRole('button', { name: 'Change' }).click()
  await shot(page, '4-change-a-part-390')
  await expectAccessible(page)
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await page.waitForTimeout(600) // the theme's colours settle before the picture is taken
  await shot(page, '5-change-a-part-dark-1280')
  await expectAccessible(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(600) // the menu has folded away before the picture is taken
  await shot(page, '6-change-a-part-dark-390')
  await expectAccessible(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to light' }).click()
})
