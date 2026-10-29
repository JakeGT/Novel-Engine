import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Impact, ImpactDependent } from '../api/client'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { EntryImpact, ImpactView, MutationImpact } from './ChangeImpact'

afterEach(() => vi.unstubAllGlobals())

function dependent(fields: Partial<ImpactDependent>): ImpactDependent {
  return {
    kind: 'scene',
    level: 'affected',
    id: 'pln_1',
    title: 'The Docks',
    reasons: ['Mara is in it'],
    plan_id: 'pln_1',
    source_id: null,
    version_id: null,
    start: null,
    end: null,
    count: 0,
    ...fields,
  }
}

const IMPACT: Impact = {
  after_change: [],
  scratch: false,
  about: ['No longer true: Mara is a member of Harbor Guild'],
  affected: [
    dependent({}),
    dependent({ kind: 'thread', id: 'pln_t', plan_id: 'pln_t', title: 'Is Mara in the guild?', reasons: ['It is about Mara'] }),
    dependent({ kind: 'rule', id: 'inv_1', plan_id: null, title: 'Guilds have members', reasons: ['It applies to Harbor Guild'] }),
    dependent({ kind: 'draft', title: 'The Docks', reasons: ['Version 2 was drafted with Mara in what was given'], version_id: 'ver_2' }),
  ],
  may_be_affected: [
    dependent({
      kind: 'text',
      level: 'may_be_affected',
      id: 'src_1',
      plan_id: null,
      source_id: 'src_1',
      version_id: 'ver_9',
      start: 0,
      end: 28,
      count: 1,
      title: 'Chapter one',
      reasons: ['Mentions Mara by name in 1 passage'],
    }),
    dependent({
      kind: 'scene_text',
      level: 'may_be_affected',
      id: 'src_2',
      source_id: 'src_2',
      version_id: 'ver_3',
      start: 10,
      end: 40,
      count: 1,
      reasons: ['Mentions Mara by name in 1 passage'],
    }),
  ],
  not_checked: ['Writing that refers to Mara without a name or alias the story bible knows, such as “she” or “the captain”.'],
}

const at = { path: 'review/:mutationId', at: 'review/mut_1' }

describe('MutationImpact', () => {
  it('says what is affected, what may be, and what was not checked, with links to each', async () => {
    mockApi({ 'GET /mutations/mut_1/impact': IMPACT })
    const { container } = renderInProject(<MutationImpact mutationId="mut_1" version="0.1." />, at)

    const section = await screen.findByRole('region', { name: 'What this change touches' })
    const affected = await within(section).findByRole('region', { name: 'Affected' })
    expect(within(affected).getByRole('heading', { level: 3 })).toHaveTextContent('Affected · 4')
    expect(within(affected).getByRole('link', { name: 'Is Mara in the guild?' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan#plan-pln_t',
    )
    expect(within(affected).getByRole('link', { name: 'Guilds have members' })).toHaveAttribute('href', '/projects/prj_1/rules')
    expect(within(affected).getByText('Version 2 was drafted with Mara in what was given')).toBeInTheDocument()
    expect(within(affected).getByText('Kept drafts')).toBeInTheDocument()

    const maybe = within(section).getByRole('region', { name: 'May be affected' })
    expect(within(maybe).getByRole('link', { name: 'Chapter one' })).toHaveAttribute('href', '/projects/prj_1/texts/src_1#at-0-28')
    expect(within(maybe).getByRole('link', { name: 'The Docks' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan/pln_1/write?at=10-40&version=ver_3',
    )
    // Never presented as the whole story.
    expect(within(section).getByRole('heading', { name: 'Not checked' })).toBeInTheDocument()
    expect(within(section).getByText(/without a name or alias/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('says plainly when nothing depends on it, and when every change is skipped', async () => {
    mockApi({ 'GET /mutations/mut_1/impact': { ...IMPACT, affected: [], may_be_affected: [] } })
    renderInProject(<MutationImpact mutationId="mut_1" version="a" />, at)
    expect(
      await screen.findByText(
        'Nothing in your plan, story rules, kept drafts, beliefs, events, pictures or studio checks depends on it, and none of your writing names it.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Affected' })).not.toBeInTheDocument()
  })

  it('cuts a long list and says how many more there are', async () => {
    const many = Array.from({ length: 11 }, (_, n) => dependent({ id: `pln_${n}`, plan_id: `pln_${n}`, title: `Scene ${n}` }))
    mockApi({ 'GET /mutations/mut_1/impact': { ...IMPACT, affected: many, may_be_affected: [] } })
    const user = userEvent.setup()
    renderInProject(<MutationImpact mutationId="mut_1" version="a" />, at)
    const affected = await screen.findByRole('region', { name: 'Affected' })
    expect(within(affected).getAllByRole('listitem')).toHaveLength(8)
    await user.click(within(affected).getByRole('button', { name: 'Show all 11' }))
    expect(within(affected).getAllByRole('listitem')).toHaveLength(11)
  })

  it('does not stand in the way when it cannot be worked out', async () => {
    mockApi({ 'GET /mutations/mut_1/impact': problem(500, 'server_error', 'Something went wrong.') })
    renderInProject(<MutationImpact mutationId="mut_1" version="a" />, at)
    expect(await screen.findByText(/could not be worked out just now. It does not stop you adding it./)).toBeInTheDocument()
  })
})

describe('EntryImpact', () => {
  it('says how much depends on the entry while it is still closed', async () => {
    const api = mockApi({ 'GET /entities/ent_1/impact': IMPACT })
    renderInProject(<EntryImpact entityId="ent_1" name="Mara" />)
    expect(await screen.findByText(/4 affected, 2 may be affected/)).toBeInTheDocument()
    expect(api.find('GET', '/entities/ent_1/impact')).toHaveLength(1)
    // The list itself stays folded away until opened, so the form comes first.
    expect(screen.queryByText('Is Mara in the guild?')).not.toBeVisible()
  })

  it('sums up what depends on an entry and lists it without leaving the form', async () => {
    const api = mockApi({ 'GET /entities/ent_1/impact': { ...IMPACT, about: ['Mara is changed'] } })
    const user = userEvent.setup()
    const { container } = renderInProject(<EntryImpact entityId="ent_1" name="Mara" />)
    await user.click(screen.getByText(/What changing Mara touches/))
    expect(api.find('GET', '/entities/ent_1/impact')).toHaveLength(1)
    expect(await screen.findByText(/4 affected, 2 may be affected/)).toBeInTheDocument()
    expect(screen.getByText('Is Mara in the guild?')).toBeInTheDocument()
    // Following a link would leave the edit unsaved, so the names are not links here.
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    await expectAccessible(container)
  })
})

describe('What a change touches beyond the plan (CON-017)', () => {
  it('links beliefs, events, pictures and studio checks to where they are', () => {
    renderInProject(
      <ImpactView
        impact={{
          after_change: [],
          scratch: false,
          about: ['No longer true: Mara is a member of Harbor Guild'],
          affected: [
            { kind: 'belief', level: 'affected', id: 'ent_t', title: 'Tobin', reasons: ['Knows what changes: Mara is a member of Harbor Guild'], count: 0 },
            { kind: 'event', level: 'affected', id: 'evt_1', title: 'The guild meeting', reasons: ['Mara takes part in it'], count: 0 },
            { kind: 'picture', level: 'affected', id: 'ast_1', title: 'Mara at the wheel', reasons: ['A picture of Mara'], count: 0 },
            { kind: 'finding', level: 'affected', id: 'pln_s1', plan_id: 'pln_s1', title: 'The Docks', reasons: ['A check says: “Mara is in two places at once.”'], count: 1 },
          ],
          may_be_affected: [],
          not_checked: ['Who learns it from whom.'],
        }}
      />,
    )
    expect(screen.getByRole('link', { name: 'Tobin' })).toHaveAttribute('href', '/projects/prj_1/entries/ent_t')
    expect(screen.getByRole('link', { name: 'The guild meeting' })).toHaveAttribute('href', '/projects/prj_1/timeline')
    expect(screen.getByRole('link', { name: 'Mara at the wheel' })).toHaveAttribute('href', '/projects/prj_1/pictures/ast_1')
    expect(screen.getByRole('link', { name: 'The Docks' })).toHaveAttribute('href', '/projects/prj_1/plan/pln_s1/write')
    expect(screen.getByText('What they believe')).toBeInTheDocument()
    expect(screen.getByText('In the studio')).toBeInTheDocument()
  })
})

describe('After the change, and as a picture (CON-017)', () => {
  const after: Impact = {
    ...IMPACT,
    scratch: true,
    after_change: [
      {
        kind: 'condition',
        plan_id: 'pln_q',
        title: 'The Quay',
        words: '“Mara must have this: it is a member of Harbor Guild.” (before the scene) would no longer be met',
        now: 'met',
        after: 'not met',
      },
      { kind: 'reader', plan_id: 'pln_m', title: 'The Market', words: 'The reader would no longer learn: Mara is a member of Harbor Guild', now: '', after: '' },
      { kind: 'reader', plan_id: 'tmp:s', title: 'The Oath', words: 'It would come into the reading order', now: '', after: '' },
    ],
  }

  it('says how conditions and the guide would read, worked out on a copy', async () => {
    const { container } = renderInProject(<ImpactView impact={after} />)
    const group = screen.getByRole('region', { name: 'After the change' })
    expect(within(group).getByText(/on a copy of your story/)).toBeInTheDocument()
    expect(within(group).getByRole('heading', { name: 'Scene conditions' })).toBeInTheDocument()
    expect(within(group).getByRole('link', { name: 'The Quay' })).toHaveAttribute('href', '/projects/prj_1/plan#plan-pln_q')
    expect(within(group).getByRole('link', { name: 'The Market' })).toHaveAttribute('href', '/projects/prj_1/plan/pln_m/guide')
    // A scene the change would add is not there yet: named, not linked.
    expect(within(group).queryByRole('link', { name: 'The Oath' })).toBeNull()
    expect(within(group).getByText('The Oath')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('says nothing would read differently, and shows no such part without a copy', () => {
    const { unmount } = renderInProject(<ImpactView impact={{ ...after, after_change: [] }} />)
    expect(screen.getByText('The plan’s conditions and the reader’s guide would read the same.')).toBeInTheDocument()
    unmount()
    renderInProject(<ImpactView impact={IMPACT} />)
    expect(screen.queryByRole('region', { name: 'After the change' })).toBeNull()
  })

  it('shows what it touches as a picture on request, with the same counts in words', async () => {
    const { container } = renderInProject(<ImpactView impact={IMPACT} />)
    expect(screen.queryByRole('img')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show it as a picture' }))
    const picture = screen.getByRole('img', { name: /What this change touches, as a picture/ })
    expect(picture).toHaveAccessibleDescription(/Scenes: 1 affected/)
    expect(picture).toHaveAccessibleDescription(/Your writing: 1 may be affected/)
    expect(screen.getByRole('button', { name: 'Hide the picture' })).toHaveAttribute('aria-expanded', 'true')
    // The lists stay: the picture is never the only way to read it.
    expect(screen.getByRole('region', { name: 'Affected' })).toBeInTheDocument()
    await expectAccessible(container)
  })
})
