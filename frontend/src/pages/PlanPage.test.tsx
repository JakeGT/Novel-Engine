import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem } from '../api/client'
import { entity, VOCABULARY } from '../test/fixtures'
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
  conditions: [] as { plan_id: string; when: string; reading: string; holds: boolean | null }[],
  items: [story, arc, beatOne, beatTwo, scene, thread],
  open: [
    { plan_id: 'pln_s', code: 'needs_place', message: 'This scene needs a place.' },
    { plan_id: 'pln_b2', code: 'needs_scene', message: 'This beat has no scene yet.' },
    { plan_id: 'pln_t', code: 'needs_payoff', message: 'This thread is opened and never resolved.' },
  ],
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
    expect(threads).toHaveTextContent('Opened in: The lamp room at night')
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
      details: {},
      expected_revision: 3,
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
        { plan_id: 'pln_s', when: 'before', reading: 'Ines Varga must have this: it is located at Kestrel Point.', holds: false },
        { plan_id: 'pln_s', when: 'after', reading: 'Ines Varga must have this: it is named “the Mapmaker”.', holds: true },
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
    const [before, after] = within(list).getAllByRole('listitem')
    expect(before).toHaveTextContent('Before Ines Varga must have this: it is located at Kestrel Point. Not true yet')
    expect(after).toHaveTextContent('True now')
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
})
