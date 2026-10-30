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
  form: 1,
  time: 'as_recorded',
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
  enforcement: 'warning',
  version: 1,
  body,
  examples: [],
  example_results: [
    { description: 'Ana in two towns', expect: 'caught', outcome: 'caught', agrees: true },
    { description: 'Ana at home', expect: 'allowed', outcome: 'allowed', agrees: true },
  ],
  broken_by: [{ entity_id: 'ent_mara', name: 'Mara', certain: true, when: '' }],
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
    expect(screen.getByText(/holds the change back until it is fixed/)).toBeInTheDocument()
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
    expect(dialog).toHaveTextContent('A rule warns you when a change would break it.')
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

  it('lets the author choose whether a rule warns or blocks, and says what that means', async () => {
    const blocking = { ...rule, enforcement: 'blocking' as const }
    const api = mockApi({ ...base, 'GET /rules': { items: [rule] }, 'PATCH /rules/inv_1': blocking })
    const user = userEvent.setup()
    const { container, unmount } = renderInProject(<RulesPage />, at)

    const choice = await screen.findByRole('group', { name: 'When a change would break One place at a time' })
    expect(within(choice).getByRole('button', { name: 'Warn me' })).toHaveAttribute('aria-pressed', 'true')
    const card = choice.closest('li') as HTMLElement
    expect(card).toHaveTextContent('you can still add the change')
    await expectAccessible(container)

    await user.click(within(choice).getByRole('button', { name: 'Block the change' }))
    await waitFor(() => expect(api.find('PATCH', '/rules/inv_1')[0].body).toEqual({ enforcement: 'blocking' }))
    expect(await screen.findByText(/now blocks any change that would break it/)).toBeInTheDocument()
    unmount()

    // Someone who can only read is told what it does, with nothing to change.
    mockApi({ ...base, 'GET /rules': { items: [blocking] } })
    renderInProject(<RulesPage />, { ...at, context: { access: VIEWER, canWrite: false } })
    const heading = await screen.findByRole('heading', { name: 'One place at a time' })
    expect(heading.closest('li')).toHaveTextContent('When a change would break it: the change is blocked.')
    expect(screen.queryByRole('button', { name: 'Block the change' })).not.toBeInTheDocument()
  })

  it('builds the richer rules by hand: two facts, a range, a belief, exceptions and story time', async () => {
    const api = mockApi({ ...base, 'GET /rules': { items: [] }, 'POST /rules/check': checked })
    const user = userEvent.setup()
    const { container } = renderInProject(<RulesPage />, at)
    const lastBody = () => (api.find('POST', '/rules/check').at(-1)?.body as { body: RuleBody }).body

    await user.click(await screen.findByRole('button', { name: 'Write your first rule' }))
    const dialog = screen.getByRole('dialog', { name: 'New story rule' })
    await user.click(await within(dialog).findByRole('button', { name: 'Build it by hand' }))
    await user.selectOptions(within(dialog).getByLabelText('Which entries the rule is about'), 'type:Character')

    // If it has one fact, it must also have another.
    await user.selectOptions(within(dialog).getByLabelText('Must or must not'), 'implies')
    await user.selectOptions(within(dialog).getByLabelText('If it has: the other entry'), 'one:ent_guild')
    await waitFor(() =>
      expect(lastBody().checks[0]).toEqual({
        kind: 'implies',
        count: 1,
        if_has: { predicate: 'LOCATED_AT', direction: 'out', other_id: 'ent_guild', other_type: null },
        pattern: { predicate: 'LOCATED_AT', direction: 'out' },
      }),
    )

    // Cannot have both: the facts already chosen carry over.
    await user.selectOptions(within(dialog).getByLabelText('Must or must not'), 'not_both')
    await waitFor(() => expect(lastBody().checks[0].not_with?.other_id).toBe('ent_guild'))
    expect(lastBody().checks[0].if_has).toBeUndefined()

    // A value within a range, with nothing a range does not use.
    await user.selectOptions(within(dialog).getByLabelText('Must or must not'), 'between')
    const high = within(dialog).getByLabelText('to')
    await user.type(high, '120')
    await waitFor(() =>
      expect(lastBody().checks[0]).toEqual({
        kind: 'between',
        count: 1,
        pattern: { predicate: 'LOCATED_AT', direction: 'out' },
        low: 0,
        high: 120,
      }),
    )

    // A belief about another entry.
    await user.selectOptions(within(dialog).getByLabelText('Must or must not'), 'believes')
    await user.selectOptions(within(dialog).getByLabelText('that'), 'ent_guild')
    await waitFor(() => expect(lastBody().checks[0]).toMatchObject({ kind: 'believes', about_id: 'ent_guild' }))
    expect(within(dialog).getByLabelText('What it believes: which way round')).toHaveTextContent('Lantern Harbor')

    // Leave out Mara and anything tagged "ghost", at every moment of the story.
    await user.click(within(dialog).getByLabelText('except some entries…'))
    await user.selectOptions(within(dialog).getByLabelText('Leave out'), 'ent_mara')
    await user.type(within(dialog).getByLabelText('and anything tagged'), 'ghost')
    await user.selectOptions(within(dialog).getByLabelText('It must hold'), 'every_moment')
    await waitFor(() => {
      const sent = lastBody()
      expect(sent.except_for).toEqual({ entity_ids: ['ent_mara'], tag: 'ghost' })
      expect(sent.time).toBe('every_moment')
    })
    expect(within(dialog).getByText(/moving from one place to another is not being in two at once/)).toBeInTheDocument()
    expect(within(dialog).getByRole('list', { name: 'Left out' })).toHaveTextContent('Mara')
    await expectAccessible(container)

    await user.click(within(dialog).getByRole('button', { name: 'Stop leaving out Mara' }))
    await waitFor(() => expect(lastBody().except_for?.entity_ids).toEqual([]))
    await user.click(within(dialog).getByLabelText('except some entries…'))
    await waitFor(() => expect(lastBody().except_for).toBeUndefined())
    expect(api.find('POST', '/assist/rules/draft')).toHaveLength(0)
  }, 30_000)

  it('says when a rule may not hold because the story times are too loose', async () => {
    const timed = {
      ...rule,
      reading: 'At every moment of the story: Every character can have at most 1 of this: it is located at something.',
      broken_by: [
        { entity_id: 'ent_mara', name: 'Mara', certain: false, when: 'around 6 in story time' },
      ],
    }
    mockApi({ ...base, 'GET /rules': { items: [timed] } })
    const { container } = renderInProject(<RulesPage />, at)
    const card = (await screen.findByRole('heading', { name: 'One place at a time' })).closest('li') as HTMLElement
    expect(card).toHaveTextContent('May be broken')
    expect(card).toHaveTextContent('May not hold for: Mara (around 6 in story time).')
    expect(card).toHaveTextContent('too loose to be sure, so this only ever warns')
    expect(card).not.toHaveTextContent('Does not hold for')
    await expectAccessible(container)
  })
})
