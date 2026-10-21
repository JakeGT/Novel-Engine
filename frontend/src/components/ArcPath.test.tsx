import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem } from '../api/client'
import { entity, fact } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, reply, VIEWER } from '../test/harness'
import { CharacterArcs } from './ArcPath'

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

const mara = entity()
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [] })
const harbor = entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const trusts = fact({
  id: 'fct_trust',
  predicate: 'KNOWS',
  object: { kind: 'entity_ref', entity_id: 'ent_tobin', project_id: 'prj_1' },
})
const docks = part({ id: 'pln_docks', title: 'The docks', position: 0 })
const bridge = part({ id: 'pln_bridge', title: 'The bridge', position: 1 })
const arc = part({
  id: 'pln_arc',
  kind: 'arc',
  title: 'Learning to trust',
  refs: [{ entity_id: 'ent_mara', role: 'about' }],
  path: [
    { name: 'Trusts no one' },
    { name: 'Trusts Tobin', scene_id: 'pln_bridge', fact_id: 'fct_trust' },
    { name: 'Leads the crew' },
  ],
})
const base = {
  'GET /entities': { items: [mara, tobin, harbor], limit: 200, offset: 0 },
  'GET /facts': { items: [trusts] },
}
const outline = {
  items: [docks, bridge, arc],
  conditions: [],
  open: [{ plan_id: 'pln_arc', code: 'arc_stage_not_placed', message: '“Leads the crew” is not placed in a scene yet.', kind: 'gap' }],
  arcs: [
    {
      arc_id: 'pln_arc',
      about: ['ent_mara'],
      stages: [
        { index: 0, name: 'Trusts no one', scene_id: null, scene_number: null, fact_id: null, fact: null },
        { index: 1, name: 'Trusts Tobin', scene_id: 'pln_bridge', scene_number: 2, fact_id: 'fct_trust', fact: 'holds' },
        { index: 2, name: 'Leads the crew', scene_id: null, scene_number: null, fact_id: null, fact: null },
      ],
    },
  ],
}
const at = { path: 'entries/:entityId', at: 'entries/ent_mara' }

describe('CharacterArcs', () => {
  it('shows each arc as stages in order, where each is reached, and what to look at', async () => {
    mockApi({ ...base, 'GET /plan': outline })
    const { container } = renderInProject(<CharacterArcs entity={mara} />, at)

    const stages = await screen.findByRole('list', { name: 'Stages of “Learning to trust”, in order' })
    const items = within(stages).getAllByRole('listitem')
    expect(items.map((item) => item.querySelector('.arc-stage-name')?.textContent)).toEqual([
      'Trusts no one',
      'Trusts Tobin',
      'Leads the crew',
    ])
    expect(items[0]).toHaveTextContent('Where it starts')
    expect(within(items[1]).getByRole('link', { name: 'The bridge' })).toHaveAttribute(
      'href',
      '/projects/prj_1/plan/pln_bridge/write',
    )
    expect(items[1]).toHaveTextContent('(scene 2)')
    expect(await within(items[1]).findByText(/Mara Venn knows Tobin Ashe/)).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('True by then')
    expect(items[2]).toHaveTextContent('Not placed in a scene yet')
    expect(screen.getByRole('list', { name: 'Worth a look in Learning to trust' })).toHaveTextContent(
      '“Leads the crew” is not placed in a scene yet.',
    )
    await expectAccessible(container)
  })

  it('starts an arc by hand with its stages, and says what is missing first', async () => {
    const api = mockApi({
      ...base,
      'GET /plan': { items: [docks, bridge], open: [], conditions: [] },
      'POST /plan': reply(201, arc),
    })
    const user = userEvent.setup()
    renderInProject(<CharacterArcs entity={mara} />, at)

    await user.click(await screen.findByRole('button', { name: 'Start an arc for Mara Venn' }))
    const dialog = screen.getByRole('dialog', { name: 'Start an arc for Mara Venn' })
    expect(within(dialog).getByLabelText('Name of the arc')).toHaveValue('Mara Venn’s arc')
    await user.click(within(dialog).getByRole('button', { name: 'Start the arc' }))
    expect(within(dialog).getAllByText('Name this stage, or remove it.')).toHaveLength(2)
    expect(api.find('POST', '/plan')).toHaveLength(0)

    const one = within(dialog).getByRole('group', { name: /Stage 1/ })
    const two = within(dialog).getByRole('group', { name: /Stage 2/ })
    await user.type(within(one).getByLabelText('Where they stand'), 'Trusts no one')
    await user.type(within(two).getByLabelText('Where they stand'), 'Trusts Tobin')
    // Scenes are offered in reading order, numbered as the checks number them.
    const scene = within(two).getByLabelText('Reached in')
    expect(within(scene).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Not placed yet',
      '1. The docks',
      '2. The bridge',
    ])
    await user.selectOptions(scene, 'pln_bridge')
    await user.selectOptions(within(two).getByLabelText('True from then on (optional)'), 'fct_trust')
    await user.click(within(dialog).getByRole('button', { name: 'Start the arc' }))

    await waitFor(() => expect(api.find('POST', '/plan')).toHaveLength(1))
    expect(api.find('POST', '/plan')[0].body).toEqual({
      kind: 'arc',
      title: 'Mara Venn’s arc',
      summary: '',
      refs: [{ entity_id: 'ent_mara', role: 'about' }],
      path: [{ name: 'Trusts no one' }, { name: 'Trusts Tobin', scene_id: 'pln_bridge', fact_id: 'fct_trust' }],
      expected_revision: 3,
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('reorders and saves the stages of an existing arc', async () => {
    const api = mockApi({ ...base, 'GET /plan': outline, 'PUT /plan/pln_arc/path': arc })
    const user = userEvent.setup()
    renderInProject(<CharacterArcs entity={mara} />, at)

    await user.click(await screen.findByRole('button', { name: 'Change the stages of Learning to trust' }))
    const dialog = screen.getByRole('dialog', { name: 'Stages of “Learning to trust”' })
    expect(within(dialog).getByRole('button', { name: 'Save the stages' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Move Stage 3 earlier' }))
    await user.click(within(dialog).getByRole('button', { name: 'Save the stages' }))

    await waitFor(() => expect(api.find('PUT', '/plan/pln_arc/path')).toHaveLength(1))
    expect(api.find('PUT', '/plan/pln_arc/path')[0].body).toEqual({
      stages: [
        { name: 'Trusts no one' },
        { name: 'Leads the crew' },
        { name: 'Trusts Tobin', scene_id: 'pln_bridge', fact_id: 'fct_trust' },
      ],
      expected_revision: 3,
    })
  })

  it('names a tied fact past the first page of facts, in the lane and in the picker', async () => {
    const late = fact({
      id: 'fct_late',
      predicate: 'KNOWS',
      object: { kind: 'entity_ref', entity_id: 'ent_tobin', project_id: 'prj_1' },
    })
    const lateArc = { ...arc, path: [{ name: 'Trusts no one' }, { name: 'Trusts Tobin', scene_id: 'pln_bridge', fact_id: 'fct_late' }] }
    const api = mockApi({
      ...base,
      'GET /facts': { items: [] },
      'GET /facts/fct_late': late,
      'GET /plan': { ...outline, items: [docks, bridge, lateArc] },
    })
    const user = userEvent.setup()
    renderInProject(<CharacterArcs entity={mara} />, at)

    const stages = await screen.findByRole('list', { name: 'Stages of “Learning to trust”, in order' })
    expect(await within(stages).findByText(/Mara Venn knows Tobin Ashe/)).toBeInTheDocument()
    expect(api.find('GET', '/facts/fct_late').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'Change the stages of Learning to trust' }))
    const dialog = screen.getByRole('dialog', { name: 'Stages of “Learning to trust”' })
    const picker = within(within(dialog).getByRole('group', { name: /Stage 2/ })).getByLabelText(
      'True from then on (optional)',
    )
    await waitFor(() => expect(picker).toHaveValue('fct_late'))
    expect(within(picker).getByRole('option', { selected: true })).toHaveTextContent('Mara Venn knows Tobin Ashe')
  })

  it('puts a stage on one of the story’s shared scales', async () => {
    const api = mockApi({
      ...base,
      'GET /plan': outline,
      'GET /plan/scales': {
        scales: [{ id: 'trust', name: 'Trust', levels: ['None', 'Wary', 'Complete'], ordered: true, one_step: false, about: '' }],
        version: 1,
      },
      'PUT /plan/pln_arc/path': arc,
    })
    const user = userEvent.setup()
    renderInProject(<CharacterArcs entity={mara} />, at)

    await user.click(await screen.findByRole('button', { name: 'Change the stages of Learning to trust' }))
    const dialog = screen.getByRole('dialog', { name: 'Stages of “Learning to trust”' })
    const two = within(dialog).getByRole('group', { name: /Stage 2/ })
    const picker = await within(two).findByLabelText('On a shared scale (optional)')
    await user.selectOptions(picker, 'trust\u0000Wary')
    await user.click(within(dialog).getByRole('button', { name: 'Save the stages' }))
    await waitFor(() => expect(api.find('PUT', '/plan/pln_arc/path')).toHaveLength(1))
    const sent = api.find('PUT', '/plan/pln_arc/path')[0].body as { stages: unknown[] }
    expect(sent.stages[1]).toEqual({
      name: 'Trusts Tobin',
      scene_id: 'pln_bridge',
      fact_id: 'fct_trust',
      scale_id: 'trust',
      level: 'Wary',
    })
  })

  it('keeps each stage, and focus, with the stage as it moves', async () => {
    mockApi({ ...base, 'GET /plan': outline })
    const user = userEvent.setup()
    renderInProject(<CharacterArcs entity={mara} />, at)

    await user.click(await screen.findByRole('button', { name: 'Change the stages of Learning to trust' }))
    const dialog = screen.getByRole('dialog', { name: 'Stages of “Learning to trust”' })
    await user.click(within(dialog).getByRole('button', { name: 'Move Stage 3 earlier' }))
    expect(within(dialog).getByRole('button', { name: 'Move Stage 2 earlier' })).toHaveFocus()
    const second = within(dialog).getByRole('group', { name: /Stage 2/ })
    expect(within(second).getByLabelText('Where they stand')).toHaveValue('Leads the crew')

    // Moved to the top, where it cannot go earlier, focus stays on the same stage.
    await user.keyboard('{Enter}')
    expect(within(dialog).getByRole('button', { name: 'Move Stage 1 later' })).toHaveFocus()
    const first = within(dialog).getByRole('group', { name: /Stage 1/ })
    expect(within(first).getByLabelText('Where they stand')).toHaveValue('Leads the crew')
  })

  it('offers no changes to a reader, and nothing at all for someone who is not a character', async () => {
    mockApi({ ...base, 'GET /plan': outline })
    renderInProject(<CharacterArcs entity={mara} />, { ...at, context: { access: VIEWER } })
    expect(await screen.findByRole('list', { name: 'Stages of “Learning to trust”, in order' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /stages/i })).not.toBeInTheDocument()

    const { container } = renderInProject(<CharacterArcs entity={harbor} />, at)
    expect(within(container).queryByRole('heading', { name: /changes/ })).not.toBeInTheDocument()
  })
})
