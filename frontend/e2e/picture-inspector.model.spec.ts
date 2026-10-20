import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { crc32, deflateSync } from 'node:zlib'

// The picture page (AST-007, AST-005, CTX-013) in a real browser against a real backend,
// with the local stand-in model (scripts/e2e/fake_model.py), which is declared able to
// see pictures and answers a description in fixed words about the first entry listed.
// Describe a picture (its cost shown first), accept a detail, offer it as a fact: it
// lands in the review queue as a suggestion, and the story is unchanged until approved.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = false) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/areaC-${name}.png`, fullPage })
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

/** A real PNG: a figure in a blue coat against a dusk sky over the water. */
function png(width: number, height: number): Buffer {
  const rows: number[] = []
  for (let y = 0; y < height; y += 1) {
    rows.push(0)
    for (let x = 0; x < width; x += 1) {
      const t = y / height
      const coat = x > width * 0.4 && x < width * 0.6 && y > height * 0.3 && y < height * 0.9
      const sea = y > height * 0.65
      rows.push(
        coat ? 40 : sea ? 30 + Math.round(30 * t) : 230 - Math.round(80 * t),
        coat ? 70 : sea ? 60 + Math.round(30 * (x / width)) : 160 - Math.round(60 * t),
        coat ? 160 : sea ? 100 : 150 - Math.round(20 * t),
      )
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

test('describe a picture, accept what was seen, and offer it for review', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const wren = (
    await call(page, 'POST', '/entities', { type: 'Character', name: 'Wren Hollis', expected_revision: await head(page) })
  ).id
  const uploaded = await page.request.fetch(
    `${API}/entities/${wren}/pictures?expected_revision=${await head(page)}&name=${encodeURIComponent('Wren on the quay')}`,
    { method: 'POST', data: png(900, 1200), headers: { 'X-CSRF-Token': await csrf(page), 'Content-Type': 'image/png' } },
  )
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy()
  const assetId = (await uploaded.json()).link.asset_id as string

  // From the media page to the picture's own page.
  await page.goto(`/projects/${PROJECT}/media`)
  await page.getByRole('button', { name: /^Wren on the quay/ }).click()
  await page.getByRole('link', { name: 'Open the picture’s page' }).click()
  await expect(page.getByRole('heading', { name: 'Wren on the quay' })).toBeVisible()
  await expect(page.getByText('Nothing has been described yet.')).toBeVisible()
  // The cost is shown before anything is sent.
  await expect(page.getByText(/About [\d,]+ tokens. The picture is sent to the assistant you use./)).toBeVisible()
  await expect(page.getByRole('radio', { name: /Use its description/ })).toBeChecked()
  await shot(page, '1-picture-page-1280', true)
  await expectAccessible(page)

  await page.getByRole('button', { name: 'Describe this picture' }).click()
  await expect(page.getByText(/Described. 3 suggestions to review below./)).toBeVisible({ timeout: 30_000 })
  const coat = page.getByRole('listitem').filter({ hasText: 'She wears a long blue coat.' })
  await expect(coat.getByText('Waiting for you')).toBeVisible()
  await expect(coat.getByText(/About Wren Hollis · Sure \(80%\)/)).toBeVisible()
  await coat.getByRole('button', { name: 'Show where' }).click()
  await expect(page.getByText(/Marked: where “She wears a long blue coat.” shows./)).toBeVisible()
  await shot(page, '2-described-marked-1280', true)
  await expectAccessible(page)

  // Accept the caption; correct the coat; reject the hair.
  await page
    .getByRole('listitem')
    .filter({ hasText: 'A woman in a long blue coat stands on a quay at dusk.' })
    .getByRole('button', { name: 'Accept' })
    .click()
  await coat.getByRole('button', { name: 'Correct' }).click()
  // While correcting, the words are in the box, so the note is found by the box.
  await page.getByLabel('Your words').fill('She wears a long blue sea coat.')
  await page.getByRole('button', { name: 'Save correction' }).click()
  const corrected = page.getByRole('listitem').filter({ hasText: 'She wears a long blue sea coat.' })
  await expect(corrected.getByText('Corrected by you')).toBeVisible()
  await page
    .getByRole('listitem')
    .filter({ hasText: 'Her hair is short and grey.' })
    .getByRole('button', { name: 'Reject' })
    .click()
  await expect(page.getByText(/Rejected and earlier suggestions \(1\)/)).toBeVisible()

  // Offer the corrected detail as a fact: a suggestion in the review queue, nothing more.
  const before = await head(page)
  await corrected.getByRole('button', { name: 'Suggest as a detail of Wren Hollis' }).click()
  const review = corrected.getByRole('link', { name: 'Open it in the review queue' })
  await expect(review).toBeVisible()
  expect(await head(page)).toBe(before)
  const facts = (await call(page, 'GET', '/facts')).items as { predicate: string }[]
  expect(facts.filter((fact) => fact.predicate === 'HAS_DETAIL')).toHaveLength(0)

  // Use it in the assistant's context: send the picture.
  await page.getByRole('radio', { name: /Send the picture/ }).check()
  await expect(page.getByText('Saved: send the picture.')).toBeVisible()
  const view = await call(page, 'GET', `/pictures/${assetId}`)
  expect(view.context_choice).toBe('send')
  expect(view.description).toBe(
    'A woman in a long blue coat stands on a quay at dusk. She wears a long blue sea coat.',
  )
  await shot(page, '3-reviewed-1280', true)

  // Narrow, light; then dark at both widths.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Wren on the quay' })).toBeVisible()
  await shot(page, '4-picture-page-390', true)
  await expectAccessible(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to dark' }).click()
  await expect(page.getByRole('heading', { name: 'Wren on the quay' })).toBeVisible()
  await shot(page, '5-picture-page-dark-1280', true)
  await expectAccessible(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await shot(page, '6-picture-page-dark-390', true)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Switch to light' }).click()

  // The suggestion waits in the review queue, from the assistant.
  await review.click()
  await expect(page.getByText(/blue sea coat/).first()).toBeVisible()
})
