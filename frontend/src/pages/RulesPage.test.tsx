import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RuleBody, RuleCheck, StoryRule } from '../api/client'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { RulesPage } from './RulesPage'

afterEach(() => vi.unstubAllGlobals())

const guild = entity({ id: 'ent_guild', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const mara = entity({ id: 'ent_mara', name: 'Mara', aliases: [] })
const body: RuleBody = {
  applies_to: { type: 'Character' },
  mode: 'all',
  checks: [{ kind: 'at_most', count: 1, pattern: { predicate: 'LOCATED_AT', direction: 'out' } }],
}
const rule: StoryRule = {
  id: 'inv_1',
  title: 'One place at a time',
  sentence: 'Nobody can be in two places at once.',
  reading: 'Every character can have at most 1 of: located at something.',
  status: 'on',
  version: 1,
  body,
  examples: [],
  example_results: [
    { description: 'Ana in two towns', expect: 'caught', outcome: 'caught', agrees: true },
    { description: 'Ana at home', expect: 'allowed', outcome: 'allowed', agrees: true },
  ],
  broken_by: [{ entity_id: 'ent_mara', name: 'Mara' }],
}
const checked: RuleCheck = {
  reading: rule.reading,
  problems: [],
  example_results: rule.example_results,
  broken_by: [],
}
const base = {
  'GET /entities': { items: [mara, guild], limit: 200, offset: 0 },
  'GET /vocabulary': VOCABULARY,
}
const at = { path: 'rules', at: 'rules' }

describe('RulesPage', () => {
  it('shows each rule as the author said it, how it is checked, and who breaks it now', async () => {
    mockApi({ ...base, 'GET /rules': { items: [rule, { ...rule, id: 'inv_2', title: 'A quiet one', status: 'paused', broken_by: [] }] } })
    const { container } = renderInProject(<RulesPage />, at)

    const card = (await screen.findByRole('heading', { name: 'One place at a time' })).closest('li') as HTMLElement
    expect(card).toHaveTextContent('“Nobody can be in two places at once.”')
    expect(card).toHaveTextContent('Checked as: Every character can have at most 1 of: located at something.')
    expect(card).toHaveTextContent('Broken now')
    expect(within(card).getByRole('link', { name: 'Mara' })).toHaveAttribute('href', '/projects/prj_1/entries/ent_mara')
    const quiet = screen.getByRole('heading', { name: 'A quiet one' }).closest('li') as HTMLElement
    expect(quiet).toHaveTextContent('Paused')
    expect(screen.getByText(/It never stops you/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('drafts a rule from a sentence, shows what it would do, and keeps it only when asked', async () => {
    const api = mockApi({
      ...base,
      'GET /rules': { items: [] },
      'POST /assist/rules/draft': {
        title: 'One place at a time',
        understood: 'A character has at most one location.',
        body,
        examples: [],
        check: checked,
        cannot_express: '',
      },
      'POST /rules/check': checked,
      'POST /rules': reply(201, rule),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<RulesPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Write your first rule' }))
    const dialog = screen.getByRole('dialog', { name: 'New story rule' })
    await user.type(within(dialog).getByLabelText(/^The rule, in your own words/), 'Nobody can be in two places at once.')
    await user.click(within(dialog).getByRole('button', { name: 'Draft it with the assistant' }))

    const how = await within(dialog).findByRole('region', { name: 'How it will be checked' })
    await waitFor(() => expect(how).toHaveTextContent('Checked as: Every character can have at most 1 of'))
    expect(how).toHaveTextContent('Would warn about: Ana in two towns')
    expect(how).toHaveTextContent('Would allow: Ana at home')
    expect(how).toHaveTextContent('Your story keeps this rule as it stands.')
    expect(dialog).toHaveTextContent('A rule only ever warns you. It never stops a change.')
    expect(api.find('POST', '/rules')).toHaveLength(0)
    await expectAccessible(container)

    await user.click(within(dialog).getByRole('button', { name: 'Keep this rule' }))
    await waitFor(() => expect(api.find('POST', '/rules')).toHaveLength(1))
    expect(api.find('POST', '/rules')[0].body).toEqual({
      title: 'One place at a time',
      sentence: 'Nobody can be in two places at once.',
      body,
      examples: [],
    })
  }, 20_000)

  it('builds a rule by hand with no assistant, and will not keep one that cannot be checked', async () => {
    const api = mockApi({
      ...base,
      'GET /rules': { items: [] },
      'POST /rules/check': (call: { body: unknown }) => {
        const sent = (call.body as { body: RuleBody }).body
        return sent.checks[0].pattern.predicate === 'HAS_NAME'
          ? { ...checked, problems: ['There is no kind of fact called “has name”.'] }
          : checked
      },
    })
    const user = userEvent.setup()
    renderInProject(<RulesPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Write your first rule' }))
    const dialog = screen.getByRole('dialog', { name: 'New story rule' })
    await user.click(await within(dialog).findByRole('button', { name: 'Build it by hand' }))
    await user.selectOptions(within(dialog).getByLabelText('Which entries the rule is about'), 'type:Character')
    await user.selectOptions(within(dialog).getByLabelText('Must or must not'), 'at_most')
    await user.type(within(dialog).getByLabelText('A short name for it'), 'One place')
    await waitFor(() => {
      const last = api.find('POST', '/rules/check').at(-1)?.body as { body: RuleBody }
      expect(last.body).toEqual({ ...body, applies_to: { entity_id: null, type: 'Character' } })
    })
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Keep this rule' })).toBeEnabled())

    // Choosing one particular entry keeps the whole of what identifies it.
    await user.selectOptions(within(dialog).getByLabelText('What must hold: the other entry'), 'one:ent_guild')
    await waitFor(() => {
      const last = api.find('POST', '/rules/check').at(-1)?.body as { body: RuleBody }
      expect(last.body.checks[0].pattern.other_id).toBe('ent_guild')
    })
    await user.selectOptions(within(dialog).getByLabelText('Which entries the rule is about'), 'one:ent_mara')
    await waitFor(() => {
      const last = api.find('POST', '/rules/check').at(-1)?.body as { body: RuleBody }
      expect(last.body.applies_to?.entity_id).toBe('ent_mara')
    })

    await user.selectOptions(within(dialog).getByLabelText('What must hold: kind of fact'), 'HAS_NAME')
    expect(await within(dialog).findByText('There is no kind of fact called “has name”.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Keep this rule' })).toBeDisabled()
    expect(api.find('POST', '/assist/rules/draft')).toHaveLength(0)
  }, 20_000)

  it('says plainly when a sentence cannot be made into a rule', async () => {
    mockApi({
      ...base,
      'GET /rules': { items: [] },
      'POST /assist/rules/draft': {
        title: 'The dead stay dead',
        understood: '',
        body: null,
        examples: [],
        check: null,
        cannot_express: 'A rule cannot speak of what happens after someone dies.',
      },
    })
    const user = userEvent.setup()
    renderInProject(<RulesPage />, at)
    await user.click(await screen.findByRole('button', { name: 'Write your first rule' }))
    const dialog = screen.getByRole('dialog', { name: 'New story rule' })
    await user.type(within(dialog).getByLabelText(/^The rule, in your own words/), 'Nobody dead may speak.')
    await user.click(within(dialog).getByRole('button', { name: 'Draft it with the assistant' }))
    expect(await within(dialog).findByRole('note')).toHaveTextContent(
      'This cannot be checked as a rule yet. A rule cannot speak of what happens after someone dies.',
    )
    expect(within(dialog).getByRole('button', { name: 'Keep this rule' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Build it by hand' })).toBeInTheDocument()
  })

  it('pauses a rule, and shows nothing that changes rules to someone who can only read', async () => {
    const api = mockApi({ ...base, 'GET /rules': { items: [rule] }, 'PATCH /rules/inv_1': { ...rule, status: 'paused' } })
    const user = userEvent.setup()
    const { unmount } = renderInProject(<RulesPage />, at)
    await user.click(await screen.findByRole('button', { name: 'Pause One place at a time' }))
    await waitFor(() => expect(api.find('PATCH', '/rules/inv_1')[0].body).toEqual({ status: 'paused' }))
    unmount()

    renderInProject(<RulesPage />, { ...at, context: { access: VIEWER, canWrite: false } })
    await screen.findByRole('heading', { name: 'One place at a time' })
    expect(screen.queryByRole('button', { name: 'New rule' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause One place at a time' })).not.toBeInTheDocument()
  })
})
