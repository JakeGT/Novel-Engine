import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { VIEWER, expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { TimelinePage } from './TimelinePage'

afterEach(() => vi.unstubAllGlobals())

const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [], attributes: {} })
const harbor = entity({ id: 'ent_harbor', name: 'Lantern Harbor', type: 'Location', aliases: [], attributes: {} })
const saltmere = entity({ id: 'ent_salt', name: 'Saltmere', type: 'Location', aliases: [], attributes: {} })

const event = {
  id: 'evt_drown',
  kind: 'death',
  name: 'Tobin drowns',
  participants: [{ entity_id: 'ent_tobin', role: 'participant' }],
  location_ids: [],
  cause_ids: [],
  outcome_ids: [],
  provenance_id: 'prv',
  created_revision: 2,
  when: { kind: 'at', at: 5 },
  premises: [],
}

const TIMELINE = {
  unit: 'year',
  placed: [{ event, when: 'year 5', earliest: 5, latest: 5, problem: null }],
  unplaced: [],
  spans: [],
  issues: [],
}

const CHECKS = {
  findings: [
    {
      check: 'life',
      code: 'after_death',
      severity: 'warning',
      message: 'Tobin Ashe is in the scene “The wake” (year 9), after their death: “Tobin drowns” (year 5).',
      records: [
        { kind: 'entry', id: 'ent_tobin', label: 'Tobin Ashe', entry_id: null },
        { kind: 'scene', id: 'pln_wake', label: 'The wake', entry_id: null },
        { kind: 'event', id: 'evt_drown', label: 'Tobin drowns', entry_id: null },
      ],
      scene_ids: ['pln_wake'],
    },
    {
      check: 'possession',
      code: 'two_holders',
      severity: 'note',
      message: 'the oar belongs to both Tobin Ashe and Mara, with no time given for either.',
      records: [],
      scene_ids: [],
    },
  ],
  coverage: [
    { check: 'travel', title: 'Not enough time to get there', covers: 'Places with no travel time are never checked.' },
  ],
  always: 'Only scenes placed in story time are checked.',
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /entities': { items: [tobin, harbor, saltmere], limit: 200, offset: 0 },
    'GET /timeline': TIMELINE,
    'GET /timeline/checks': CHECKS,
    'GET /timeline/how-long': { travel: [], lasting: [], version: 0 },
    ...extra,
  }
}

describe('TimelinePage continuity', () => {
  it('lists what cannot all be true apart from notes, with links to each record', async () => {
    mockApi(routes())
    const { container } = renderInProject(<TimelinePage />)

    const list = await screen.findByRole('list', { name: 'Continuity findings' })
    const [warning, note] = within(list).getAllByRole('listitem')
    expect(within(warning).getByText('Cannot all be true')).toBeInTheDocument()
    expect(within(note).getByText('Worth a look')).toBeInTheDocument()
    expect(within(warning).getByRole('link', { name: 'Tobin Ashe' })).toHaveAttribute(
      'href',
      '/projects/prj_1/entries/ent_tobin',
    )
    expect(within(warning).getByRole('link', { name: 'The wake' })).toHaveAttribute('href', '/projects/prj_1/plan#plan-pln_wake')
    expect(within(warning).getByRole('link', { name: 'Tobin drowns' })).toHaveAttribute(
      'href',
      '/projects/prj_1/timeline#event-evt_drown',
    )
    expect(screen.getByText(/1 thing that cannot all be true, 1 note/)).toBeInTheDocument()
    expect(screen.queryByText(/not shown/)).not.toBeInTheDocument()
    // What each check covers is one click away and says so.
    expect(screen.getByText('What these checks look at (1)')).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('says how many more were found than are listed', async () => {
    mockApi(routes({ 'GET /timeline/checks': { ...CHECKS, not_shown: 12 } }))
    renderInProject(<TimelinePage />)

    expect(await screen.findByText(/12 more are not shown/)).toBeInTheDocument()
  })

  it('saves the author’s own travel times, keeping what was typed when it is refused', async () => {
    const api = mockApi(
      routes({
        'PUT /timeline/how-long': problem(409, 'conflict', 'Someone else changed how long things take since you opened it.'),
      }),
    )
    const user = userEvent.setup()
    const { container } = renderInProject(<TimelinePage />)

    const form = await screen.findByRole('form', { name: 'How long things take' })
    await user.click(await within(form).findByRole('button', { name: 'Add a journey' }))
    const journey = within(form).getByRole('group', { name: 'Journey 1' })
    await user.selectOptions(within(journey).getByRole('combobox', { name: 'From' }), 'ent_harbor')
    await user.selectOptions(within(journey).getByRole('combobox', { name: 'To' }), 'ent_harbor')
    await user.type(within(journey).getByRole('textbox', { name: 'takes' }), '3')
    await user.click(within(form).getByRole('button', { name: 'Save how long things take' }))
    // Checked before sending, next to the table.
    expect(await within(form).findByRole('alert')).toHaveTextContent('A journey is between two different places.')
    expect(api.find('PUT', '/timeline/how-long')).toHaveLength(0)

    await user.selectOptions(within(journey).getByRole('combobox', { name: 'To' }), 'ent_salt')
    await user.click(within(form).getByRole('button', { name: 'Save how long things take' }))
    await waitFor(() => expect(api.find('PUT', '/timeline/how-long')).toHaveLength(1))
    expect(api.find('PUT', '/timeline/how-long')[0].body).toEqual({
      travel: [{ from_id: 'ent_harbor', to_id: 'ent_salt', amount: 3 }],
      lasting: [],
      expected_version: 0,
    })
    expect(await within(form).findByText(/Someone else changed/)).toBeInTheDocument()
    expect(within(journey).getByRole('textbox', { name: 'takes' })).toHaveValue('3')

    // How long an event lasts: placed events are offered.
    await user.click(within(form).getByRole('button', { name: 'Say how long something lasts' }))
    const length = within(form).getByRole('group', { name: 'Length 1' })
    expect(within(length).getByRole('option', { name: 'Tobin drowns' })).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('shows the table without changing it to someone who can only read', async () => {
    mockApi(
      routes({
        'GET /timeline/how-long': {
          travel: [{ from_id: 'ent_harbor', to_id: 'ent_salt', amount: 2 }],
          lasting: [],
          version: 1,
        },
      }),
    )
    renderInProject(<TimelinePage />, { context: { access: VIEWER } })
    expect(await screen.findByText('Lantern Harbor to Saltmere: 2 years')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a journey' })).not.toBeInTheDocument()
  })

  it('offers the kinds of event the checks read when adding one', async () => {
    const api = mockApi(
      routes({
        'POST /events': (call: { body: unknown }) => ({ ...event, ...(call.body as object), id: 'evt_new' }),
        'GET /projection': { head_revision: 3 },
      }),
    )
    const user = userEvent.setup()
    renderInProject(<TimelinePage />)
    await user.click(await screen.findByRole('button', { name: 'Add an event' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: 'What happened' }), 'Saltmere burns')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: /What kind of event/ }), 'destroyed')
    await user.click(within(dialog).getByRole('button', { name: 'Add the event' }))
    await waitFor(() => expect(api.find('POST', '/events')).toHaveLength(1))
    expect((api.find('POST', '/events')[0].body as { kind: string }).kind).toBe('destroyed')
  })
})
