import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// Writing in the scene studio with no AI provider: typing @ offers story-bible entries and
// writes the chosen one as a mark the name finder reads (CTX-003); a scene shows pictures
// chosen from the story's picture library or uploaded onto it (AST-EXP-018).
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = false) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/areaB-${name}.png`, fullPage })
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

async function call(page: Page, method: string, path: string, data?: unknown, raw?: Buffer) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data: raw ?? data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': raw ? 'image/png' : 'application/json' },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number

/** A small real PNG: a soft band of colour, so the screenshots show a picture, not a blank. */
function png(width: number, height: number, [r, g, b]: [number, number, number]): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(body) >>> 0)
    return Buffer.concat([length, body, crc])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header.set([8, 2, 0, 0, 0], 8)
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const shade = 0.55 + 0.45 * (y / height)
      rows.push(Math.round(r * shade), Math.round(g * shade), Math.round(b * (1 - x / width / 3)))
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(Buffer.from(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

async function signIn(page: Page) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()
}

test('typing @ names an entry, and a scene shows pictures from the library', async ({ page }) => {
  await signIn(page)
  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Calloway'],
    ['tern', 'Location', 'Tern Lighthouse'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  const scene = await call(page, 'POST', '/plan', {
    kind: 'scene',
    title: 'The Lamp Goes Out',
    summary: 'Wren climbs the tower in the dark.',
    refs: [
      { entity_id: ids.wren, role: 'point_of_view' },
      { entity_id: ids.tern, role: 'place' },
    ],
    expected_revision: await head(page),
  })
  // A picture of the lighthouse, then taken off it: shown nowhere, still in the library.
  const upload = await page.request.fetch(
    `${API}/entities/${ids.tern}/pictures?expected_revision=${await head(page)}&name=Tern%20at%20dusk`,
    {
      method: 'POST',
      data: png(96, 64, [90, 120, 160]),
      headers: {
        'X-CSRF-Token': (await page.context().cookies()).find((c) => c.name === 'novel_csrf')?.value ?? '',
        'Content-Type': 'image/png',
      },
    },
  )
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const onEntry = (await upload.json()).link
  await call(page, 'POST', `/links/${onEntry.id}/retire`, { expected_revision: await head(page) })

  await page.goto(`/projects/${PROJECT}/plan/${scene.id}/write`)
  const editor = page.getByRole('textbox', { name: /^The text of/ })
  await editor.click()
  await editor.pressSequentially('The stairs of @te')
  const list = page.getByRole('listbox', { name: 'Entries in your story bible' })
  await expect(list).toBeVisible()
  await expect(list.getByRole('option').first()).toHaveText(/Tern Lighthouse/)
  await expect(editor).toHaveAttribute('aria-activedescendant', /.+/)
  await shot(page, 'mention-list')
  await expectAccessible(page)
  await page.keyboard.press('Enter')
  await expect(editor).toHaveValue('The stairs of @"Tern Lighthouse" ')
  // Put in by the browser's own editing, so undo takes the name back out.
  await page.keyboard.press('Control+z')
  await expect(editor).toHaveValue('The stairs of @te')
  await page.keyboard.press('Control+Shift+z')
  await expect(editor).toHaveValue('The stairs of @"Tern Lighthouse" ')
  await editor.pressSequentially('turned under @Wr')
  await expect(list).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(editor).toHaveValue('The stairs of @"Tern Lighthouse" turned under @"Wren Calloway" ')
  await editor.pressSequentially('and @Wr')
  await page.keyboard.press('Escape')
  await expect(list).toBeHidden()
  await expect(editor).toHaveValue('The stairs of @"Tern Lighthouse" turned under @"Wren Calloway" and @Wr')
  await editor.pressSequentially('en climbed.')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/Saved as version 1/)).toBeVisible()

  // Pictures: the one shown nowhere is offered, and an upload lands on the scene.
  await page.getByRole('button', { name: 'Add a picture' }).click()
  const panel = page.getByRole('region', { name: 'Pictures for this scene' })
  await expect(panel.getByText('Not shown anywhere')).toBeVisible()
  await panel.getByRole('button', { name: 'Use “Tern at dusk” for “The Lamp Goes Out”' }).click()
  await expect(panel.getByText('“Tern at dusk” is now shown on this scene.')).toBeVisible()
  await panel
    .getByLabel('Choose a picture for “The Lamp Goes Out”')
    .setInputFiles({ name: 'Wren on the stairs.png', mimeType: 'image/png', buffer: png(64, 96, [170, 110, 70]) })
  await expect(panel.getByText('“Wren on the stairs” is now shown on this scene.')).toBeVisible()
  const strip = page.getByRole('list', { name: 'Pictures for “The Lamp Goes Out”' })
  await expect(strip.getByRole('img')).toHaveCount(2)
  await expect(strip.getByRole('img', { name: 'Wren on the stairs' })).toBeVisible()
  await shot(page, 'pictures-panel', true)
  await expectAccessible(page)

  // Removing takes it off the scene and offers Undo; the picture stays in the library.
  await panel.getByRole('button', { name: 'Remove “Tern at dusk” from “The Lamp Goes Out”' }).click()
  await expect(panel.getByText(/no longer shown on this scene/)).toBeVisible()
  await expect(strip.getByRole('img')).toHaveCount(1)
  await panel.getByRole('button', { name: 'Undo' }).click()
  await expect(strip.getByRole('img')).toHaveCount(2)
  await panel.getByRole('button', { name: 'Close' }).click()
  await page.evaluate(() => window.scrollTo(0, 0))
  await shot(page, 'studio-head')

  // The pictures stay after a reload, and the head holds at phone width.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(strip.getByRole('img')).toHaveCount(2)
  await shot(page, 'studio-phone')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await editor.click()
  await editor.press('End')
  await editor.pressSequentially(' @')
  await expect(list).toBeVisible()
  await shot(page, 'mention-phone')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})
