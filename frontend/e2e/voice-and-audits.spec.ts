import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The author's voice, drift across an audit, scheduled audits, a what-if's differences,
// settling a finding in the story bible, and what a change touches (CON-018, CON-011,
// CON-010, CON-017), in a real browser against a real backend with no AI provider: every
// path here is free, and the ones that need the assistant are shown up to their cost.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/voice-${name}.png`, fullPage })
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

async function call(page: Page, method: string, path: string, data?: unknown, headers: Record<string, string> = {}) {
  const cookies = await page.context().cookies()
  const csrf = cookies.find((cookie) => cookie.name === 'novel_csrf')?.value ?? ''
  const response = await page.request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json', ...headers },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page, headers: Record<string, string> = {}) =>
  (await call(page, 'GET', '/projection', undefined, headers)).head_revision as number

// Wren Haddow's scenes: three in the author's usual voice, one told with "I" in the present.
const USUAL = [
  'Wren Haddow walked down to the Saltmarsh Ferry before the light came up over the estuary.',
  'She counted the crates on the deck and found one more than the manifest said.',
  'Pell Varrick was waiting by the rail with his collar turned up against the wind.',
  '"You are late," he said, and she shrugged and set her bag down beside him.',
  'They watched the gulls fight over a crust somebody had dropped on the boards.',
  'He had brought bread, and she took half of it without asking.',
  'The tide was turning, and the smell of it reached them before the sound did.',
  'It was cold, but neither of them wanted to be the first to say so.',
  'When the bell rang across the water she stood and brushed the salt from her coat.',
  'He did not ask where she was going, and she did not tell him.',
]
const ODD = [
  'I walk down to the Saltmarsh Ferry before the light comes up, and I count the crates, because counting is the only thing I can do while the water keeps moving.',
  'Pell Varrick waits by the rail, and I sit beside him, and I take the bread he brings, a testament to patience.',
  'I watch the gulls and I think about the manifest and the crate nobody wrote down.',
]

function prose(lines: string[], words: number, seed: number): string {
  let text = ''
  let n = seed
  while (text.split(/\s+/).length < words) {
    n = (n * 9301 + 49297) % 233280
    const start = n % lines.length
    text += [...lines.slice(start), ...lines.slice(0, start)].join(' ') + '\n\n'
  }
  return text
}

test('how a scene sounds, drift across an audit, schedules and a what-if’s differences', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['wren', 'Character', 'Wren Haddow'],
    ['pell', 'Character', 'Pell Varrick'],
    ['ferry', 'Location', 'Saltmarsh Ferry'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  ids.trust = (
    await call(page, 'POST', '/facts', {
      proposition: {
        subject_id: ids.wren,
        predicate: 'KNOWS',
        object: { kind: 'entity_ref', entity_id: ids.pell, project_id: PROJECT },
      },
      expected_revision: await head(page),
    })
  ).id
  const beat = await call(page, 'POST', '/plan', {
    kind: 'beat',
    title: 'The ferry crossings',
    expected_revision: await head(page),
  })
  const scenes: { id: string; title: string }[] = []
  for (const [index, title] of ['First crossing', 'Second crossing', 'Third crossing', 'Fourth crossing'].entries()) {
    const scene = await call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      parent_id: beat.id,
      refs: [
        { entity_id: ids.wren, role: 'point_of_view' },
        { entity_id: ids.pell, role: 'participant' },
        { entity_id: ids.ferry, role: 'place' },
      ],
      expected_revision: await head(page),
    })
    const text = index === 3 ? prose(ODD, 700, index) : prose(USUAL, 700, index)
    await call(page, 'PUT', `/plan/${scene.id}/studio/text`, { content: text, base_version_id: null })
    scenes.push(scene)
  }

  // The studio's margin: how the fourth scene sounds against the author's usual.
  await page.goto(`/projects/${PROJECT}/plan/${scenes[3].id}/write`)
  const voice = page.getByRole('region', { name: 'How it sounds' })
  await expect(voice.getByText(/told with “I” in the narration/)).toBeVisible()
  await expect(voice.getByText(/present tense/)).toBeVisible()
  await expect(voice.getByRole('table', { name: 'This scene and your usual' })).toBeVisible()
  await expectAccessible(page)
  await voice.scrollIntoViewIfNeeded()
  await shot(page, '1-studio-voice', false)
  // The box follows what is saved, so it changes once the story has kept the choice.
  const counted = voice.getByRole('checkbox', { name: /Count this scene in your usual/ })
  await counted.click()
  await expect(counted).not.toBeChecked()
  await counted.click()
  await expect(counted).toBeChecked()

  // A finding can be settled in the story bible; its cost is shown before anything is sent.
  const checks = page.getByRole('region', { name: 'Checks' })
  await checks.getByRole('button', { name: 'Settle it in the story bible…' }).first().click()
  const settle = page.getByRole('group', { name: 'Settle it in the story bible' })
  await expect(settle.getByText(/Your words are not changed/)).toBeVisible()
  await expect(settle.getByText(/About [\d,]+ tokens\./)).toBeVisible()
  await expect(settle.getByRole('button', { name: 'Draft the change' })).toBeVisible()
  await settle.scrollIntoViewIfNeeded()
  await expectAccessible(page)
  await shot(page, '2-settle-finding', false)
  await settle.getByRole('button', { name: 'Cancel' }).click()

  // On a phone, and in the dark.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await voice.scrollIntoViewIfNeeded()
  await shot(page, '3-studio-voice-390', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.reload()
  await voice.scrollIntoViewIfNeeded()
  await shot(page, '4-studio-voice-390-dark', false)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.setViewportSize({ width: 1280, height: 900 })

  // From the plan: the audit dialog offers a schedule; then a free audit of the beat.
  await page.goto(`/projects/${PROJECT}/plan`)
  await page.getByLabel('Move, plan or remove The ferry crossings').click()
  await page.getByRole('button', { name: 'Audit this part of the story: The ferry crossings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Audit “The ferry crossings”' })
  await expect(dialog.getByText('4 written scenes')).toBeVisible()
  for (const box of await dialog.getByRole('group', { name: 'What the assistant checks in each scene' }).getByRole('checkbox').all()) {
    if (await box.isChecked()) await box.uncheck()
  }
  await dialog.getByText('Run this audit on a schedule').click()
  await dialog.getByLabel('How often').selectOption('7')
  await dialog.getByRole('button', { name: 'Run it on this schedule' }).click()
  await expect(dialog.getByText(/Scheduled. The first run is/)).toBeVisible()
  await expectAccessible(page)
  await shot(page, '5-audit-schedule', false)
  await dialog.getByRole('button', { name: 'Start the audit' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Audit of “The ferry crossings”' })).toBeVisible()
  const drift = page.getByRole('region', { name: 'Voice and relationships across these scenes' })
  await expect(drift.getByRole('table', { name: 'Each scene’s voice, in reading order' })).toBeVisible({ timeout: 60_000 })
  await expect(drift.getByRole('row', { name: /Fourth crossing/ })).toContainText('told with “I”')
  await expect(drift.getByText('Wren Haddow and Pell Varrick')).toBeVisible()
  const corrections = page.getByRole('region', { name: 'Correction plan' })
  await expect(corrections.getByText(/Reads \d+ findings? across \d+ scenes?: about [\d,]+ tokens\./)).toBeVisible()
  await expectAccessible(page)
  await drift.scrollIntoViewIfNeeded()
  await shot(page, '6-audit-drift')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await drift.scrollIntoViewIfNeeded()
  await shot(page, '7-audit-drift-390', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1280, height: 900 })

  // A what-if, told differently: its differences, with the checks and the voice.
  const whatIf = await call(page, 'POST', '/what-ifs', { name: 'Wren stays ashore' })
  const inWhatIf = { 'X-Story-Branch': whatIf.id }
  const studio = await call(page, 'GET', `/plan/${scenes[1].id}/studio`, undefined, inWhatIf)
  await call(
    page,
    'PUT',
    `/plan/${scenes[1].id}/studio/text`,
    { content: prose(ODD, 700, 9), base_version_id: studio.version_id },
    inWhatIf,
  )
  await call(page, 'POST', `/facts/${ids.trust}/close`, { expected_revision: await head(page, inWhatIf) }, inWhatIf)
  await page.goto(`/projects/${PROJECT}/what-ifs/${whatIf.id}`)
  await page.getByRole('link', { name: 'Audit the differences' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'How “Wren stays ashore” differs from your story' })).toBeVisible()
  const written = page.getByRole('region', { name: 'Scenes written differently' })
  await expect(written.getByRole('link', { name: 'Second crossing' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'The story bible' })).toContainText('Wren Haddow knows Pell Varrick')
  await expectAccessible(page)
  await shot(page, '8-what-if-differences')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(written.getByRole('link', { name: 'Second crossing' })).toBeVisible()
  await shot(page, '9-what-if-differences-390', false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1280, height: 900 })

  // What a change touches now reaches what characters believe and events.
  await call(page, 'POST', '/beliefs', {
    holder_entity_id: ids.pell,
    source_ref: 'author',
    proposition: {
      subject_id: ids.wren,
      predicate: 'KNOWS',
      object: { kind: 'entity_ref', entity_id: ids.pell, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  await call(page, 'POST', '/events', {
    kind: 'meeting',
    name: 'The night crossing',
    participants: [{ entity_id: ids.wren, role: 'participant' }],
    expected_revision: await head(page),
  })
  const proposed = await call(page, 'POST', '/mutations', {
    origin: 'model',
    expected_revision: await head(page),
    operations: [{ op: 'close_fact', id: ids.trust }],
  })
  await page.goto(`/projects/${PROJECT}/review/${proposed.id}`)
  const touches = page.getByRole('region', { name: 'What this change touches' })
  const affected = touches.getByRole('region', { name: 'Affected', exact: true })
  await expect(affected.getByRole('link', { name: 'Pell Varrick' })).toBeVisible()
  await expect(affected).toContainText('Knows what changes: Wren Haddow knows Pell Varrick')
  await expect(affected.getByRole('link', { name: 'The night crossing' })).toBeVisible()
  await expectAccessible(page)
  await touches.scrollIntoViewIfNeeded()
  await shot(page, '10-change-touches', false)
})
