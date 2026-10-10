import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SchemaVersion } from '../api/client'
import { VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply, VIEWER } from '../test/harness'
import { CustomizePage } from './CustomizePage'

afterEach(() => vi.unstubAllGlobals())

const definition = {
  label: 'Project schema',
  description: '',
  entity_extensions: [
    { entity_type: 'Ship', description: null, fields: [{ name: 'tonnage', field_type: 'integer', required: true }] },
  ],
  predicate_extensions: [],
}

function version(overrides: Partial<SchemaVersion> = {}): SchemaVersion {
  return {
    id: 'scv_1',
    workspace_id: 'ws_1',
    project_id: 'prj_1',
    version: 1,
    status: 'published',
    definition,
    content_hash: 'abc',
    compatibility: 'additive',
    created_at: '2026-10-09T10:00:00Z',
    published_at: '2026-10-09T10:00:00Z',
    ...overrides,
  } as SchemaVersion
}

const report = {
  valid: true,
  content_hash: 'h',
  compatibility: 'additive',
  errors: [],
  migration_plan: [],
  entity_types: [],
  predicates: [],
}
const base = { 'GET /schemas': [version()], 'GET /schemas/vocabulary': VOCABULARY }

describe('CustomizePage', () => {
  it('lists types and kinds of fact in plain words', async () => {
    mockApi(base)
    const { container } = renderInProject(<CustomizePage />)

    const ship = (await screen.findByText('Ship')).closest('li') as HTMLElement
    expect(ship).toHaveTextContent('Yours')
    expect(ship).toHaveTextContent('Tonnage (whole number)')
    expect(screen.getByText('Character').closest('li')).toHaveTextContent('Built in')
    const facts = screen.getByRole('region', { name: 'Kinds of fact' })
    expect(within(facts).getAllByRole('listitem')[0]).toHaveTextContent('Character is located at location')
    await expectAccessible(container)
  })

  it('adds a type through a form: checked, saved and published in one step', async () => {
    const api = mockApi({
      ...base,
      'POST /schemas/validate': report,
      'POST /schemas/drafts': reply(201, { version: version({ id: 'scv_2', version: 2, status: 'draft' }), compatibility: 'additive', migration_plan: [] }),
      'POST /schemas/scv_2/publish': version({ id: 'scv_2', version: 2 }),
    })
    const user = userEvent.setup()
    renderInProject(<CustomizePage />)

    await user.click(await screen.findByRole('button', { name: 'Add a type' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a type of entry' })
    await user.type(within(dialog).getByLabelText('What is it called?'), 'sea spell')
    await user.click(within(dialog).getByRole('button', { name: 'Add a field' }))
    await user.type(within(dialog).getByLabelText('Field 1 name'), 'Power Level')
    await user.selectOptions(within(dialog).getByLabelText('Field 1 kind'), 'One of a list')
    await user.click(within(dialog).getByRole('button', { name: 'Add type' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('List the choices for “Power Level”')

    await user.type(within(dialog).getByLabelText('Field 1 choices'), 'low, high')
    await user.click(within(dialog).getByRole('button', { name: 'Add type' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const sent = (api.find('POST', '/schemas/drafts')[0].body as { definition: typeof definition }).definition
    // The existing type is kept and the new one is appended.
    expect(sent.entity_extensions).toEqual([
      definition.entity_extensions[0],
      {
        entity_type: 'SeaSpell',
        description: null,
        fields: [{ name: 'power_level', field_type: 'enum', required: false, values: ['low', 'high'] }],
      },
    ])
    expect(api.find('POST', '/schemas/scv_2/publish')).toHaveLength(1)
  })

  it('never publishes a change that would put existing entries at risk', async () => {
    const api = mockApi({
      ...base,
      'POST /schemas/validate': {
        ...report,
        compatibility: 'migration_required',
        migration_plan: ['Backfill tonnage on 3 ships'],
      },
    })
    const user = userEvent.setup()
    renderInProject(<CustomizePage />)

    await user.click(await screen.findByRole('button', { name: 'Edit Ship' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Ship' })
    expect(within(dialog).getByLabelText('What is it called?')).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Save type' }))

    expect(await within(dialog).findByText('Existing entries would need updating first')).toBeInTheDocument()
    expect(within(dialog).getByText(/Backfill tonnage on 3 ships/)).toBeInTheDocument()
    expect(api.find('POST', '/schemas/drafts')).toHaveLength(0)
  })

  it('adds a kind of fact as a phrase between two types', async () => {
    const api = mockApi({
      ...base,
      'POST /schemas/validate': report,
      'POST /schemas/drafts': reply(201, { version: version({ id: 'scv_2', version: 2, status: 'draft' }), compatibility: 'additive', migration_plan: [] }),
      'POST /schemas/scv_2/publish': version({ id: 'scv_2', version: 2 }),
    })
    const user = userEvent.setup()
    renderInProject(<CustomizePage />)

    await user.click(await screen.findByRole('button', { name: 'Add a kind of fact' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a kind of fact' })
    await user.type(within(dialog).getByLabelText(/The phrase in the middle/), 'was trained by')
    await user.click(within(dialog).getByRole('button', { name: 'Add kind of fact' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Choose at least one type it can be about')

    const about = within(dialog).getByRole('group', { name: 'Who or what can it be about?' })
    await user.click(within(about).getByLabelText('Character'))
    const pointsTo = within(dialog).getByRole('group', { name: 'Which types of entry?' })
    await user.click(within(pointsTo).getByLabelText('Character'))
    await user.click(within(dialog).getByRole('button', { name: 'Add kind of fact' }))

    await waitFor(() => expect(api.find('POST', '/schemas/drafts')).toHaveLength(1))
    const sent = (api.find('POST', '/schemas/drafts')[0].body as { definition: Record<string, unknown[]> }).definition
    expect(sent.predicate_extensions).toEqual([
      {
        predicate: 'WAS_TRAINED_BY',
        description: null,
        subject_types: ['Character'],
        object_kind: 'entity_ref',
        object_types: ['Character'],
        cardinality: 'many',
        temporal_allowed: false,
      },
    ])
  })

  it('keeps the raw definition editor for advanced changes and reports a refused publish', async () => {
    mockApi({
      'GET /schemas': [version(), version({ id: 'scv_2', version: 2, status: 'draft' })],
      'GET /schemas/vocabulary': VOCABULARY,
      'POST /schemas/validate': { ...report, valid: false, compatibility: 'invalid', errors: ['label is required'] },
      'POST /schemas/scv_2/publish': problem(409, 'schema_incompatible', 'This schema change is forbidden.'),
    })
    const user = userEvent.setup()
    renderInProject(<CustomizePage />)

    await user.click(await screen.findByText(/Advanced: versions and the raw definition/))
    await user.click(screen.getByRole('button', { name: 'Check definition' }))
    expect(await screen.findByText('The definition is not valid.')).toBeInTheDocument()
    expect(screen.getByText(/label is required/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save as draft' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Publish version 2' }))
    const alert = await screen.findByText(/This version cannot be published/)
    expect(alert.closest('[role="alert"]')).toHaveTextContent('Your story was not changed')
  })

  it('shows the setup to a reader without any way to change it', async () => {
    mockApi(base)
    renderInProject(<CustomizePage />, { context: { access: VIEWER } })
    await screen.findByText('Ship')
    expect(screen.queryByRole('button', { name: 'Add a type' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Ship' })).not.toBeInTheDocument()
  })
})
