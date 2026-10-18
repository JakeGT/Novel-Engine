import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem } from '../api/client'
import { entity, fact, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { PlanPage } from './PlanPage'

afterEach(() => vi.unstubAllGlobals())

function part(overrides: Partial<PlanItem>): PlanItem {
  return {
    id: 'pln_x',
    kind: 'scene',
    title: 'A part',
    summary: '',
    parent_id: null,
    position: 0,
    refs: [],
    sets_up: [],
    pays_off: [],
    details: {},
    conditions: [],
    created_revision: 1,
    ...overrides,
  }
}

const ines = entity({ id: 'ent_ines', name: 'Ines Varga', aliases: [] })
const point = entity({ id: 'ent_point', type: 'Location', name: 'Kestrel Point', aliases: [] })
const everyone = { 'GET /entities': { items: [ines, point], limit: 200, offset: 0 } }
const at = { path: 'plan', at: 'plan' }

const story = part({ id: 'pln_story', kind: 'story', title: 'The Kestrel Light', summary: 'A mapmaker charts the shoals.' })
const arc = part({ id: 'pln_arc', kind: 'arc', title: 'Ines learns the truth', parent_id: 'pln_story', refs: [{ entity_id: 'ent_ines', role: 'about' }] })
const beatOne = part({ id: 'pln_b1', kind: 'beat', title: 'She finds the false chart', parent_id: 'pln_arc', position: 0 })
const beatTwo = part({ id: 'pln_b2', kind: 'beat', title: 'She confronts her father', parent_id: 'pln_arc', position: 1 })
const thread = part({ id: 'pln_t', kind: 'thread', title: 'Who drew the false chart?' })
const scene = part({
  id: 'pln_s',
  kind: 'scene',
  title: 'The lamp room at night',
  parent_id: 'pln_b1',
  summary: 'Ines compares the two charts.',
  refs: [{ entity_id: 'ent_ines', role: 'point_of_view' }],
  sets_up: ['pln_t'],
})
const outline = {
  conditions: [] as Record<string, unknown>[],
  items: [story, arc, beatOne, beatTwo, scene, thread],
  open: [
    { plan_id: 'pln_s', code: 'needs_place', message: 'This scene needs a place.' },
    { plan_id: 'pln_b2', code: 'needs_scene', message: 'This beat has no scene yet.' },
    { plan_id: 'pln_t', code: 'needs_payoff', message: 'This thread is opened and never resolved.' },
  ],
  threads: [{ thread_id: 'pln_t', steps: [{ plan_id: 'pln_s', step: 'opens' }] }],
}

describe('PlanPage', () => {
  it('reads as an outline, with who and where in ordinary words and what is still open', async () => {
    mockApi({ ...everyone, 'GET /plan': outline })
    const { container } = renderInProject(<PlanPage />, at)

    const tree = (await screen.findByRole('heading', { name: 'Outline' })).closest('section') as HTMLElement
    const titles = within(tree).getAllByRole('heading', { level: 3 }).map((node) => node.textContent)
    // Top to bottom: the story, its arc, each beat with its scenes under it.
    expect(titles).toEqual([
      'The Kestrel Light',
      'Ines learns the truth',
      'She finds the false chart',
      'The lamp room at night',
      'She confronts her father',
    ])
    const lamp = within(tree).getByRole('heading', { name: 'The lamp room at night' }).closest('li') as HTMLElement
    expect(lamp).toHaveTextContent('Seen by Ines Varga')
    expect(lamp).toHaveTextContent('Opens: Who drew the false chart?')
    expect(within(lamp).getByRole('list', { name: 'Still to do for The lamp room at night' })).toHaveTextContent(
      'This scene needs a place.',
    )

    // Threads are kept apart, and say where they were opened.
    const threads = screen.getByRole('heading', { name: 'Threads' }).closest('section') as HTMLElement
    expect(
      within(threads).getByRole('list', { name: 'How “Who drew the false chart?” runs through the plan' }),
    ).toHaveTextContent('Opens The lamp room at night')
    expect(threads).toHaveTextContent('This thread is opened and never resolved.')
    // Gaps are things to do, said as such.
    expect(screen.getByText('3 things the plan still needs')).toBeInTheDocument()
    expect(screen.getByText(/gaps to fill when you are ready, not mistakes/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('adds a scene under a beat by hand, with nothing required but a title', async () => {
    const api = mockApi({ ...everyone, 'GET /plan': outline, 'POST /plan': reply(201, part({ id: 'pln_new' })) })
    const user = userEvent.setup()
    renderInProject(<PlanPage />, at)

    await user.click(await screen.findByRole('button', { name: 'Add a scene under She confronts her father' }))
    const form = screen.getByRole('form', { name: 'New scene' })
    await user.click(within(form).getByRole('button', { name: 'Add scene to the plan' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('Give it a title.')
    expect(api.find('POST', '/plan')).toHaveLength(0)

    await user.type(within(form).getByLabelText('Title'), 'On the gallery')
    await user.selectOptions(within(form).getByLabelText(/^Point of view/), 'ent_ines')
    await user.selectOptions(within(form).getByLabelText(/^Place/), 'ent_point')
    await user.selectOptions(within(form).getByLabelText(/^Threads it resolves/), 'pln_t')
    await user.click(within(form).getByRole('button', { name: 'Add scene to the plan' }))

    await waitFor(() => expect(api.find('POST', '/plan')).toHaveLength(1))
    expect(api.find('POST', '/plan')[0].body).toEqual({
      kind: 'scene',
      parent_id: 'pln_b2',
      title: 'On the gallery',
      summary: '',
      refs: [
        { entity_id: 'ent_ines', role: 'point_of_view' },
        { entity_id: 'ent_point', role: 'place' },
      ],
      sets_up: [],
      pays_off: ['pln_t'],
      advances: [],
      details: {},
      expected_revision: 3,
    })
  })

  it('shows a thread as steps in outline order, and lets a scene move it along', async () => {
    const middle = part({ id: 'pln_m', title: 'The harbour office', parent_id: 'pln_b2', advances: ['pln_t'] })
    const last = part({ id: 'pln_l', title: 'The father’s study', parent_id: 'pln_b2', position: 1, pays_off: ['pln_t'] })
    const api = mockApi({
      ...everyone,
      'GET /plan': {
        ...outline,
        items: [...outline.items, middle, last],
        open: [],
        threads: [
          {
            thread_id: 'pln_t',
            steps: [
              { plan_id: 'pln_s', step: 'opens' },
              { plan_id: 'pln_m', step: 'advances' },
              { plan_id: 'pln_l', step: 'resolves' },
            ],
          },
        ],
      },
      'PATCH /plan/pln_s': scene,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PlanPage />, at)
    const steps = await screen.findByRole('list', { name: 'How “Who drew the false chart?” runs through the plan' })
    expect(within(steps).getAllByRole('listitem').map((node) => node.textContent)).toEqual([
      'Opens The lamp room at night',
      'Moves along The harbour office',
      'Resolves The father’s study',
    ])
    expect(within(steps).getByRole('link', { name: 'The harbour office' })).toHaveAttribute('href', '#plan-pln_m')
    const office = screen.getByRole('heading', { name: 'The harbour office' }).closest('li') as HTMLElement
    expect(office).toHaveTextContent('Moves along: Who drew the false chart?')
    await expectAccessible(container)

    // In the form, a thread this scene opens is not offered as one it moves along.
    const lamp = screen.getByRole('heading', { name: 'The lamp room at night' }).closest('li') as HTMLElement
    await user.click(within(lamp).getByRole('button', { name: 'Edit The lamp room at night' }))
    const form = screen.getByRole('form', { name: 'Change The lamp room at night' })
    const along = within(form).getByLabelText(/^Threads it moves along/)
    expect(within(along).queryByRole('option', { name: 'Who drew the false chart?' })).not.toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Remove Who drew the false chart?' }))
    await user.selectOptions(within(form).getByLabelText(/^Threads it moves along/), 'pln_t')
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.find('PATCH', '/plan/pln_s')).toHaveLength(1))
    const body = api.find('PATCH', '/plan/pln_s')[0].body as Record<string, unknown>
    expect(body.sets_up).toEqual([])
    expect(body.advances).toEqual(['pln_t'])
  })

  it('shows what the viewpoint wants in a scene, and lets the author say it by hand', async () => {
    const wanting = { ...scene, intent: { goal: 'find the true chart', obstacle: 'her father keeps the key', outcome: 'yes_but' as const } }
    const api = mockApi({
      ...everyone,
      'GET /plan': { ...outline, items: [story, arc, beatOne, beatTwo, wanting, thread] },
      'PATCH /plan/pln_s': wanting,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PlanPage />, at)
    const intent = await screen.findByLabelText('What the viewpoint wants in The lamp room at night')
    expect(intent).toHaveTextContent('Wantsfind the true chart')
    expect(intent).toHaveTextContent('In the wayher father keeps the key')
    expect(intent).toHaveTextContent('EndsYes, but — they get it, with a cost or a new complication')
    await expectAccessible(container)

    const lamp = screen.getByRole('heading', { name: 'The lamp room at night' }).closest('li') as HTMLElement
    await user.click(within(lamp).getByRole('button', { name: 'Edit The lamp room at night' }))
    const form = screen.getByRole('form', { name: 'Change The lamp room at night' })
    const group = within(form).getByRole('group', { name: 'What the viewpoint character wants here (optional)' })
    expect(within(group).getByLabelText(/^Wants/)).toHaveValue('find the true chart')
    await user.clear(within(group).getByLabelText(/^In the way/))
    await user.type(within(group).getByLabelText(/^In the way/), 'the fog')
    await user.selectOptions(within(group).getByLabelText(/^How it ends/), 'no_and')
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.find('PATCH', '/plan/pln_s')).toHaveLength(1))
    expect((api.find('PATCH', '/plan/pln_s')[0].body as Record<string, unknown>).intent).toEqual({
      goal: 'find the true chart',
      obstacle: 'the fog',
      outcome: 'no_and',
    })
  })

  it('reorders beside its neighbours and removes with a plain warning about what goes with it', async () => {
    const api = mockApi({
      ...everyone,
      'GET /plan': outline,
      'POST /plan/pln_b2/move': beatTwo,
      'POST /plan/pln_arc/remove': { revision: 4, removed: 4 },
    })
    const user = userEvent.setup()
    renderInProject(<PlanPage />, at)

    const second = (await screen.findByRole('heading', { name: 'She confronts her father' })).closest('li') as HTMLElement
    await user.click(within(second).getByText('Move, plan, remove'))
    expect(within(second).getByRole('button', { name: 'Move down' })).toBeDisabled()
    await user.click(within(second).getByRole('button', { name: 'Move up' }))
    await waitFor(() => expect(api.find('POST', '/move')).toHaveLength(1))
    expect(api.find('POST', '/move')[0].body).toEqual({ parent_id: 'pln_arc', before_id: 'pln_b1', expected_revision: 3 })

    const arcRow = screen.getByRole('heading', { name: 'Ines learns the truth' }).closest('li') as HTMLElement
    await user.click(within(arcRow).getAllByText('Move, plan, remove')[0])
    await user.click(within(arcRow).getAllByRole('button', { name: 'Remove' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Remove “Ines learns the truth” from the plan?' })
    expect(dialog).toHaveTextContent('It has 2 parts directly under it, and everything under those goes too.')
    expect(dialog).toHaveTextContent('Your story bible is not affected')
    await user.click(within(dialog).getByRole('button', { name: 'Remove it and what is under it' }))
    await waitFor(() => expect(api.find('POST', '/remove')).toHaveLength(1))
  })

  it('says a plan is optional when there is none, and offers a reader nothing to change', async () => {
    mockApi({ ...everyone, 'GET /plan': { items: [], open: [] } })
    const first = renderInProject(<PlanPage />, at)
    expect(await screen.findByText('No plan yet')).toBeInTheDocument()
    expect(screen.getByText(/You can write without one/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start with the premise' })).toBeInTheDocument()
    first.unmount()

    mockApi({ ...everyone, 'GET /plan': outline })
    renderInProject(<PlanPage />, { ...at, context: { access: VIEWER } })
    await screen.findByRole('heading', { name: 'Outline' })
    expect(screen.queryByRole('button', { name: /Edit|Add|Remove|Thread/ })).not.toBeInTheDocument()
  })

  it('opens a scene in the studio from the plan, for writing or (for a reader) reading', async () => {
    mockApi({ ...everyone, 'GET /plan': outline, 'GET /reader': { steps: [], facts: {} } })
    const user = userEvent.setup()
    const first = renderInProject(<PlanPage />, at)
    const lamp = (await screen.findByRole('heading', { name: 'The lamp room at night' })).closest('li') as HTMLElement
    // Only scenes are written; the way there is in view, not tucked in a menu.
    expect(screen.getAllByRole('link', { name: /^Write this scene/ })).toHaveLength(1)
    const write = within(lamp).getByRole('link', { name: 'Write this scene: The lamp room at night' })
    expect(write).toHaveTextContent('Write this scene')
    await expectAccessible(first.container)
    await user.click(write)
    expect(screen.getAllByTestId('location')[0]).toHaveTextContent('/projects/prj_1/plan/pln_s/write')
    first.unmount()

    renderInProject(<PlanPage />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByRole('link', { name: 'Read this scene: The lamp room at night' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan/pln_s/write',
    )
  })

  it('shows what must be true around a scene, and lets the author set it by hand', async () => {
    const withConditions = {
      ...outline,
      conditions: [
        {
          plan_id: 'pln_s',
          when: 'before',
          reading: 'Ines Varga must have this: it is located at Kestrel Point.',
          holds: false,
          strength: 'hard',
          status: 'not_met',
          note: 'Not true in the story bible when this scene happens.',
          index: 0,
        },
        {
          plan_id: 'pln_s',
          when: 'after',
          reading: 'Ines Varga must have this: it is named “the Mapmaker”.',
          holds: true,
          strength: 'hard',
          status: 'met',
          note: '',
          index: 1,
        },
        { plan_id: 'pln_s', when: 'before', reading: 'The storm has broken.', holds: null, strength: 'soft', status: 'unchecked', note: '', index: 2 },
      ],
    }
    const api = mockApi({
      ...everyone,
      'GET /plan': withConditions,
      'GET /vocabulary': VOCABULARY,
      'PUT /plan/pln_s/conditions': scene,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<PlanPage />, at)

    const list = await screen.findByRole('list', { name: 'Before and after The lamp room at night' })
    const [before, after, words] = within(list).getAllByRole('listitem')
    expect(before).toHaveTextContent(
      'Before · required Ines Varga must have this: it is located at Kestrel Point. Not true yet Not true in the story bible when this scene happens.',
    )
    expect(after).toHaveTextContent('Recorded')
    expect(words).toHaveTextContent('Before · preferred The storm has broken. Not checked')
    await expectAccessible(container)

    await user.click(screen.getByText('Move, plan, remove', {
        selector: 'summary[aria-label="Move, plan or remove The lamp room at night"]',
      }))
    await user.click(screen.getByRole('button', { name: 'Before and after' }))
    const dialog = screen.getByRole('dialog', { name: 'Before and after “The lamp room at night”' })
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Add something that must be true first' }))
    await user.selectOptions(within(dialog).getByLabelText('What must be true: the other entry'), 'one:ent_point')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.find('PUT', '/conditions')).toHaveLength(1))
    expect(api.find('PUT', '/conditions')[0].body).toEqual({
      conditions: [
        {
          when: 'before',
          strength: 'hard',
          entity_id: 'ent_ines',
          check: {
            kind: 'has',
            count: 1,
            pattern: { predicate: 'LOCATED_AT', direction: 'out', other_id: 'ent_point', other_type: null },
          },
        },
      ],
      expected_revision: 3,
    })
  }, 20_000)
  it('puts what is out of order first, and lists the promises the reader is owed', async () => {
    const inPoint = fact({ id: 'fct_in', subject_id: 'ent_ines', object: { kind: 'entity_ref', entity_id: 'ent_point', project_id: 'prj_1' } })
    const checked = {
      ...outline,
      open: [
        {
          plan_id: 'pln_s',
          code: 'reveals_too_early',
          kind: 'problem',
          message: 'This shows the reader “Ines Varga is located at Kestrel Point” plainly, before “The end”, where it is meant to come out.',
        },
        ...outline.open.map((entry) => ({ ...entry, kind: 'gap' })),
      ],
      promises: [
        { kind: 'thread', subject_id: 'pln_t', made_in: 'pln_s', made_at: 1, moved_along: [], kept_in: null, kept_at: null, distance: 3, out_of_order: false },
        { kind: 'clue', subject_id: 'fct_in', made_in: 'pln_s', made_at: 1, moved_along: [], kept_in: 'pln_s', kept_at: 1, distance: 0, out_of_order: false },
      ],
    }
    mockApi({ ...everyone, 'GET /plan': checked, 'GET /reader': { steps: [], facts: { fct_in: inPoint } } })
    const { container } = renderInProject(<PlanPage />, at)

    const problems = await screen.findByRole('list', { name: 'Out of order or not ready' })
    expect(problems).toHaveTextContent('plainly, before “The end”, where it is meant to come out.')
    // With something out of order, the list is opened by itself.
    expect(problems.closest('details')).toHaveAttribute('open')
    expect(screen.getByRole('list', { name: 'Gaps to fill' })).toHaveTextContent('This beat has no scene yet.')
    expect(screen.getByText(/1 out of order or not ready, 3 gaps to fill/)).toBeInTheDocument()
    const inline = screen.getByRole('list', { name: 'Still to do for The lamp room at night' })
    expect(within(inline).getAllByRole('listitem')[0]).toHaveTextContent('Worth a look: This shows the reader')

    expect(screen.getByRole('link', { name: '1 promise to the reader still owed' })).toHaveAttribute('href', '#promises-heading')
    const owed = screen.getByRole('list', { name: 'Promises still owed' })
    expect(owed).toHaveTextContent('Who drew the false chart?')
    expect(owed).toHaveTextContent('Opened in The lamp room at night (scene 1)')
    expect(owed).toHaveTextContent('Still owed — 3 scenes since, to the end of the plan')
    const kept = screen.getByRole('list', { name: 'Promises kept' })
    await waitFor(() => expect(kept).toHaveTextContent('Clue Ines Varga is located at Kestrel Point'))
    expect(kept).toHaveTextContent('Shown plainly in The lamp room at night (scene 1) — in the same scene')
    await expectAccessible(container)
  })

  it('says when there are no promises yet and how to make one', async () => {
    mockApi({ ...everyone, 'GET /plan': { ...outline, promises: [] }, 'GET /reader': { steps: [], facts: {} } })
    renderInProject(<PlanPage />, at)
    expect(await screen.findByRole('heading', { name: 'Promises to the reader' })).toBeInTheDocument()
    expect(screen.getByText(/No promises yet/)).toBeInTheDocument()
  })

  it('takes a condition in the author’s own words, as preferred, and says what is missing', async () => {
    const api = mockApi({
      ...everyone,
      'GET /plan': outline,
      'GET /vocabulary': VOCABULARY,
      'GET /facts': { items: [], limit: 200, offset: 0 },
      'PUT /plan/pln_s/conditions': scene,
    })
    const user = userEvent.setup()
    renderInProject(<PlanPage />, at)
    await user.click(
      await screen.findByText('Move, plan, remove', {
        selector: 'summary[aria-label="Move, plan or remove The lamp room at night"]',
      }),
    )
    await user.click(screen.getByRole('button', { name: 'Before and after' }))
    const dialog = screen.getByRole('dialog', { name: 'Before and after “The lamp room at night”' })
    await user.click(within(dialog).getByRole('button', { name: 'Add something that must be true first' }))
    await user.selectOptions(
      within(dialog).getByLabelText('How it is said (Before this scene, condition 1)'),
      'words',
    )
    await user.selectOptions(within(dialog).getByLabelText('How much it matters (Before this scene, condition 1)'), 'soft')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    // Nothing is sent while the words are empty, and the field says why.
    expect(within(dialog).getByText('Write what must be true, or remove this one.')).toBeInTheDocument()
    expect(api.find('PUT', '/conditions')).toHaveLength(0)
    await expectAccessible(dialog)

    await user.type(within(dialog).getByLabelText('What must be true, in your words'), 'The storm has broken')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.find('PUT', '/conditions')).toHaveLength(1))
    expect(api.find('PUT', '/conditions')[0].body).toEqual({
      conditions: [{ when: 'before', strength: 'soft', words: 'The storm has broken' }],
      expected_revision: 3,
    })
  }, 20_000)

  it('marks the scene where a fact is meant to come out', async () => {
    const inPoint = fact({ id: 'fct_in', subject_id: 'ent_ines', object: { kind: 'entity_ref', entity_id: 'ent_point', project_id: 'prj_1' } })
    const api = mockApi({
      ...everyone,
      'GET /plan': outline,
      'GET /reader': { steps: [], facts: {} },
      'GET /facts': { items: [inPoint], limit: 200, offset: 0 },
      'PUT /plan/pln_s/reveals': scene,
    })
    const user = userEvent.setup()
    renderInProject(<PlanPage />, at)
    await user.click(
      await screen.findByText('Move, plan, remove', {
        selector: 'summary[aria-label="Move, plan or remove The lamp room at night"]',
      }),
    )
    await user.click(screen.getByRole('button', { name: 'What the reader learns' }))
    const dialog = screen.getByRole('dialog', { name: 'What the reader learns in “The lamp room at night”' })
    await user.click(await within(dialog).findByRole('checkbox'))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: /How the reader learns/ }), 'main')
    await expectAccessible(dialog)
    await user.click(within(dialog).getByRole('button', { name: 'Save what the reader learns' }))
    await waitFor(() => expect(api.find('PUT', '/reveals')).toHaveLength(1))
    expect(api.find('PUT', '/reveals')[0].body).toEqual({
      reveals: [{ fact_id: 'fct_in', how: 'shown', main_reveal: true }],
      expected_revision: 3,
    })
  }, 20_000)
})
