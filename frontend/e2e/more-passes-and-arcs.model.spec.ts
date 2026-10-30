import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Batch 10, area C, in a real browser against a real backend with the local stand-in
// model (scripts/e2e/fake_model.py):
// - an optional check (bodies, objects and cause) chosen in the studio, its cost shown,
//   its note quoting the words (CON-008);
// - the assistant's voice check of the passages the voice measures flagged (CON-018);
// - an arc's stages suggested from the plan and kept through Review (STY-006);
// - a scene suggested for a thread nothing moves along (STY-004);
// - a condition about what someone has seen, checked against what is recorded (STY-005).
// The story is shared with every other browser test, so every name here is its own.
// Set E2E_SHOTS to a folder to keep a screenshot of each step for a design review.

const PROJECT = 'local-project'
const API = `/api/v1/projects/${PROJECT}`
const SHOTS = process.env.E2E_SHOTS

async function shot(page: Page, name: string, target?: ReturnType<Page['locator']>) {
  if (!SHOTS) return
  if (target) await target.screenshot({ path: `${SHOTS}/C-${name}.png` })
  else await page.screenshot({ path: `${SHOTS}/C-${name}.png`, fullPage: true })
}

/** What is on screen once the target is scrolled into view: for a panel inside a scrolling column. */
async function view(page: Page, name: string, target: ReturnType<Page['locator']>) {
  if (!SHOTS) return
  await target.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/C-${name}.png` })
}

async function expectAccessible(page: Page) {
  await page.waitForTimeout(350)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
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
  return response.status() === 204 ? null : response.json()
}
const head = async (page: Page) => (await call(page, 'GET', '/projection')).head_revision as number
const keep = async (page: Page, mutationId: string) => {
  await call(page, 'POST', `/mutations/${mutationId}/approve`, {})
  await call(page, 'POST', `/mutations/${mutationId}/commit`)
}

const BARN = 'Bryn Talloway waited in the salt barn after dark.\n\nShe counted the sacks twice. One was missing.'
const THIRD =
  'Bryn walked down to the salt barn before the light came up over the marsh. ' +
  'She looked at the sacks and counted the ones the carters had left in the night. ' +
  'Osk was waiting by the door with his coat buttoned against the wind. ' +
  '"You are late," he said, and she shrugged and sat down on the step beside him. ' +
  'They watched the gulls fight over a crust somebody had dropped in the yard. ' +
  'He had brought bread and she took half of it without asking for it. '
const FIRST =
  'I walk down to the salt barn before the light comes up, and I look at the sacks, and I ' +
  'count the ones that come back, because counting is the only thing I can do while the ' +
  'marsh keeps moving and the gulls keep screaming over the yard and nobody comes. '
const long = (paragraph: string) => {
  let text = ''
  while (text.split(/\s+/).length < 700) text += paragraph
  return text
}

test('optional checks, the voice check, arc stages, a gap and what someone has seen', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  await expect(page.getByText(/Version \d+ · saved/)).toBeVisible()

  const ids: Record<string, string> = {}
  for (const [key, type, name] of [
    ['bryn', 'Character', 'Bryn Talloway'],
    ['osk', 'Character', 'Osk Marrow'],
    ['barn', 'Location', 'The Salt Barn'],
  ]) {
    ids[key] = (await call(page, 'POST', '/entities', { type, name, expected_revision: await head(page) })).id
  }
  await call(page, 'POST', '/facts', {
    proposition: {
      subject_id: ids.osk,
      predicate: 'LOCATED_AT',
      object: { kind: 'entity_ref', entity_id: ids.barn, project_id: PROJECT },
    },
    expected_revision: await head(page),
  })
  const scene = async (title: string, summary: string, extra: Record<string, unknown> = {}) =>
    call(page, 'POST', '/plan', {
      kind: 'scene',
      title,
      summary,
      refs: [{ entity_id: ids.bryn, role: 'point_of_view' }],
      expected_revision: await head(page),
      ...extra,
    })
  const thread = await call(page, 'POST', '/plan', {
    kind: 'thread',
    title: 'Who emptied the salt sacks?',
    expected_revision: await head(page),
  })
  const theft = await scene('The empty sacks', 'Bryn finds the salt sacks emptied overnight.', { sets_up: [thread.id] })
  const market = await scene('The salt market', 'Bryn sells what salt is left to Osk Marrow.')
  const reckoning = await scene('The reckoning at the barn', 'Bryn names the one who emptied the sacks.', {
    pays_off: [thread.id],
  })
  const arc = await call(page, 'POST', '/plan', {
    kind: 'arc',
    title: 'Bryn learns to ask for help',
    summary: 'Bryn stops carrying the barn alone and lets Osk help her.',
    refs: [{ entity_id: ids.bryn, role: 'about' }],
    expected_revision: await head(page),
  })

  // The studio: an optional check is off until ticked, with its cost beside it.
  await call(page, 'PUT', `/plan/${theft.id}/studio/text`, { content: BARN, base_version_id: null })
  await page.goto(`/projects/${PROJECT}/plan/${theft.id}/write`)
  const choices = page.getByRole('group', { name: 'What the assistant checks' })
  const physical = choices.getByRole('checkbox', { name: /Bodies, objects and cause/ })
  await expect(physical).not.toBeChecked()
  await expect(choices.getByText('More you can add. Each is one more request, and is off until you tick it.')).toBeVisible()
  await physical.check()
  await expect(page.getByText(/About [\d,]+ tokens for the 6 chosen\./)).toBeVisible()
  await shot(page, '01-studio-optional-checks', choices)
  await expectAccessible(page)
  await page.getByRole('button', { name: /Check with the assistant/ }).click()
  const notes = page.getByRole('region', { name: /The assistant.s notes/ })
  await expect(notes.getByText(/no lamp is near her, so she could not count/)).toBeVisible({ timeout: 30_000 })
  await expect(notes.getByText('Bodies, objects and cause').first()).toBeVisible()
  await view(page, '02-studio-physical-note', notes.getByText(/no lamp is near her/))

  // How it sounds: four scenes in the author's usual voice, one told with "I".
  const voices: { id: string }[] = []
  for (const [title, text] of [
    ['Salt barn morning one', long(THIRD)],
    ['Salt barn morning two', long(THIRD)],
    ['Salt barn morning three', long(THIRD)],
    ['Salt barn in the first person', long(FIRST)],
  ]) {
    const made = await scene(title, 'Bryn waits at the salt barn.')
    await call(page, 'PUT', `/plan/${made.id}/studio/text`, { content: text, base_version_id: null })
    voices.push(made)
  }
  await page.goto(`/projects/${PROJECT}/plan/${voices[3].id}/write`)
  const voice = page.getByRole('region', { name: 'How it sounds' })
  await expect(voice.getByText(/told with “I”/).first()).toBeVisible()
  await voice.getByRole('button', { name: 'Ask the assistant about the passages that show it' }).click()
  const ask = voice.getByRole('group', { name: 'Ask the assistant how these passages sound' })
  await expect(ask.getByText(/About [\d,]+ tokens/)).toBeVisible()
  await view(page, '03-voice-offer', ask)
  await expectAccessible(page)
  await ask.getByRole('button', { name: 'Ask the assistant' }).click()
  await expect(ask.getByText(/Done: 1 note from the assistant/)).toBeVisible({ timeout: 30_000 })
  await expect(notes.getByText(/told with “I” in the present tense/)).toBeVisible()
  await view(page, '04-voice-note', notes.getByText(/told with “I” in the present tense/))

  // The plan: the arc's stages, suggested with their cost first and kept through Review.
  await page.goto(`/projects/${PROJECT}/plan`)
  const arcRow = page.locator(`#plan-${arc.id}`)
  await arcRow.getByRole('button', { name: 'Suggest the stages' }).click()
  const stagesOffer = arcRow.getByRole('group', { name: 'Suggest the stages' })
  await expect(stagesOffer.getByText(/About [\d,]+ tokens/)).toBeVisible()
  await shot(page, '05-stages-offer', arcRow)
  await expectAccessible(page)
  await stagesOffer.getByRole('button', { name: 'Ask the assistant' }).click()
  const stagesLink = stagesOffer.getByRole('link', { name: 'See the suggestion in Review' })
  await expect(stagesLink).toBeVisible({ timeout: 30_000 })
  const stagesMutation = (await stagesLink.getAttribute('href'))!.split('/').pop()!
  await stagesLink.click()
  await expect(page.getByText('Opening the suggested changes…')).toHaveCount(0)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByText(/Keeps to herself/).first()).toBeVisible()
  await shot(page, '06-stages-review')
  await keep(page, stagesMutation)
  await page.goto(`/projects/${PROJECT}/plan`)
  const lane = page.getByRole('list', { name: 'Stages of “Bryn learns to ask for help”, in order' })
  await expect(lane).toContainText('Keeps to herself')
  await expect(lane).toContainText('Asks for help')

  // A thread nothing moves along: one scene suggested, placed before the reckoning.
  const threadRow = page.locator(`#plan-${thread.id}`)
  await expect(threadRow.getByText(/no scene in between moves it along/)).toBeVisible()
  await threadRow.getByRole('button', { name: 'Suggest a scene' }).click()
  const gapOffer = threadRow.getByRole('group', { name: 'Suggest a scene' })
  await expect(gapOffer.getByText(/just before the scene that resolves it/)).toBeVisible()
  await shot(page, '07-gap-offer', threadRow)
  await gapOffer.getByRole('button', { name: 'Ask the assistant' }).click()
  const gapLink = gapOffer.getByRole('link', { name: 'See the suggestion in Review' })
  await expect(gapLink).toBeVisible({ timeout: 30_000 })
  await keep(page, (await gapLink.getAttribute('href'))!.split('/').pop()!)
  await page.reload()
  const sceneTitles = page.locator('.plan-item.kind-scene > .plan-head .plan-title')
  await expect(sceneTitles.filter({ hasText: 'A torn sack on the stair' })).toHaveCount(1)
  const titles = await sceneTitles.allTextContents()
  expect(titles.indexOf('A torn sack on the stair')).toBe(titles.indexOf('The reckoning at the barn') - 1)
  await expect(page.locator(`#plan-${thread.id}`).getByText(/no scene in between moves it along/)).toHaveCount(0)
  expect(market.id).toBeTruthy()

  // What someone has seen: a condition on the reckoning, checked against what is recorded.
  await page.getByLabel('Move, plan or remove The reckoning at the barn').click()
  await page.locator(`#plan-${reckoning.id}`).getByRole('button', { name: 'Before and after' }).click()
  const dialog = page.getByRole('dialog', { name: 'Before and after “The reckoning at the barn”' })
  await dialog.getByRole('button', { name: 'Add something that must be true first' }).click()
  await dialog.getByLabel('How it is said (Before this scene, condition 1)').selectOption('knows')
  await dialog.getByLabel('Who (Before this scene, condition 1)').selectOption({ label: 'Bryn Talloway' })
  await dialog.getByLabel('Knows, has seen or has heard (Before this scene, condition 1)').selectOption('seen')
  const statement = dialog.getByLabel('What they must know')
  const value = await statement.locator('option', { hasText: 'Osk Marrow' }).first().getAttribute('value')
  await statement.selectOption(value!)
  await shot(page, '08-knowing-condition', dialog)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toHaveCount(0)
  const condition = page.locator(`#plan-${reckoning.id} .plan-conditions li`).first()
  await expect(condition).toContainText('Bryn Talloway must have seen this: Osk Marrow is located at The Salt Barn.')
  await expect(condition).toContainText('Nothing records that Bryn Talloway knows this.')
  await shot(page, '09-knowing-unmet', page.locator(`#plan-${reckoning.id}`))

  // Phone width, and dark.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(lane).toBeVisible()
  await shot(page, '10-plan-390', page.locator(`#plan-${arc.id}`))
  await page.goto(`/projects/${PROJECT}/plan/${theft.id}/write`)
  await expect(choices).toBeVisible()
  await shot(page, '11-studio-390', choices)
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/projects/${PROJECT}/plan`)
  await expect(lane).toBeVisible()
  await shot(page, '12-plan-dark', page.locator(`#plan-${reckoning.id}`))
  await expectAccessible(page)
})
