import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, type RecordedCall, problem, renderInProject, reply } from '../test/harness'
import { EntityForm } from './EntityForm'

afterEach(() => vi.unstubAllGlobals())

const base = {
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
}

describe('EntityForm', () => {
  it('derives typed, validated attribute fields from the published schema', async () => {
    const api = mockApi({
      ...base,
      'POST /entities': (call: RecordedCall) => (reply(201, entity({ ...(call.body as object), id: 'ent_new' }))),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<EntityForm />)

    // The type list comes from the vocabulary, including the project's own type.
    await screen.findByRole('option', { name: 'Ship (project type)' })
    await user.selectOptions(screen.getByLabelText('Type'), 'Ship')
    const attributes = screen.getByRole('group', { name: 'Ship attributes' })
    expect(within(attributes).getByLabelText('tonnage (required)')).toBeInTheDocument()
    expect(within(attributes).getByRole('combobox', { name: 'rig' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Name'), 'Dawn Runner')
    await user.type(within(attributes).getByLabelText('tonnage (required)'), 'heavy')
    await user.type(within(attributes).getByLabelText('motto'), 'much too long a motto')
    await user.click(screen.getByRole('button', { name: 'Create entity' }))

    // Invalid input is caught before any request, and tied to its field.
    const tonnage = within(attributes).getByLabelText('tonnage (required)')
    expect(tonnage).toHaveAttribute('aria-invalid', 'true')
    expect(tonnage).toHaveAccessibleDescription('tonnage must be a whole number.')
    expect(api.find('POST', '/entities')).toHaveLength(0)
    await expectAccessible(container)

    await user.clear(tonnage)
    await user.type(tonnage, '120')
    await user.clear(within(attributes).getByLabelText('motto'))
    await user.selectOptions(within(attributes).getByRole('combobox', { name: 'rig' }), 'brig')
    await user.click(screen.getByRole('button', { name: 'Create entity' }))

    await waitFor(() => expect(api.find('POST', '/entities')).toHaveLength(1))
    expect(api.find('POST', '/entities')[0].body).toEqual({
      type: 'Ship',
      name: 'Dawn Runner',
      slug: null,
      aliases: [],
      attributes: { tonnage: 120, rig: 'brig' },
      expected_revision: 3,
    })
  })

  it('offers a free-form key/value editor for core types', async () => {
    const api = mockApi({
      ...base,
      'POST /entities': reply(201, entity()),
    })
    const user = userEvent.setup()
    renderInProject(<EntityForm />)

    await user.type(await screen.findByLabelText('Name'), 'Mara')
    await user.type(screen.getByLabelText('Aliases'), 'The Fox, Venn')
    await user.click(screen.getByRole('button', { name: 'Add attribute' }))
    await user.type(screen.getByLabelText('Attribute 1 name'), 'age')
    await user.type(screen.getByLabelText('Attribute 1 value'), '30')
    await user.click(screen.getByRole('button', { name: 'Add attribute' }))
    await user.type(screen.getByLabelText('Attribute 2 name'), 'role')
    await user.type(screen.getByLabelText('Attribute 2 value'), 'pilot')
    await user.click(screen.getByRole('button', { name: 'Create entity' }))

    await waitFor(() => expect(api.find('POST', '/entities')).toHaveLength(1))
    expect(api.find('POST', '/entities')[0].body).toMatchObject({
      type: 'Character',
      aliases: ['The Fox', 'Venn'],
      attributes: { age: 30, role: 'pilot' },
    })
  })

  it('warns about a duplicate name without blocking the save', async () => {
    const api = mockApi({
      ...base,
      'GET /entities/name-collisions': { items: [entity({ id: 'ent_other' })], limit: 1, offset: 0 },
      'POST /entities': reply(201, entity()),
    })
    const user = userEvent.setup()
    renderInProject(<EntityForm />)

    await user.type(await screen.findByLabelText('Name'), 'Mara Venn')
    expect(await screen.findByText(/Names are not identity/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create entity' }))
    await waitFor(() => expect(api.find('POST', '/entities')).toHaveLength(1))
  })

  it('keeps everything the author entered on a stale head and resubmits against the new one', async () => {
    let attempts = 0
    const api = mockApi({
      ...base,
      'POST /entities': () => {
        attempts += 1
        return attempts === 1
          ? problem(409, 'revision_conflict', 'The branch head changed; reload and retry.')
          : reply(201, entity())
      },
    })
    const user = userEvent.setup()
    const { context } = renderInProject(<EntityForm />)

    await user.type(await screen.findByLabelText('Name'), 'Mara Venn')
    await user.type(screen.getByLabelText('Slug'), 'mara')
    await user.click(screen.getByRole('button', { name: 'Add attribute' }))
    await user.type(screen.getByLabelText('Attribute 1 name'), 'role')
    await user.type(screen.getByLabelText('Attribute 1 value'), 'pilot')
    await user.click(screen.getByRole('button', { name: 'Create entity' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Canon changed while you were editing')
    expect(alert).toHaveTextContent('everything you entered is still here')
    // Nothing the author typed was lost.
    expect(screen.getByLabelText('Name')).toHaveValue('Mara Venn')
    expect(screen.getByLabelText('Slug')).toHaveValue('mara')
    expect(screen.getByLabelText('Attribute 1 value')).toHaveValue('pilot')
    expect(api.find('POST', '/entities')[0].body).toMatchObject({ expected_revision: 3 })

    await user.click(screen.getByRole('button', { name: 'Reload head and resubmit' }))

    await waitFor(() => expect(api.find('POST', '/entities')).toHaveLength(2))
    expect(context.refreshHead).toHaveBeenCalledTimes(1)
    expect(api.find('POST', '/entities')[1].body).toMatchObject({
      name: 'Mara Venn',
      slug: 'mara',
      attributes: { role: 'pilot' },
      expected_revision: 5,
    })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('keeps entered data when the server rejects the content', async () => {
    mockApi({
      ...base,
      'POST /entities': problem(422, 'validation_error', '[slug_conflict] slug is used by another entity'),
    })
    const user = userEvent.setup()
    renderInProject(<EntityForm />)

    await user.type(await screen.findByLabelText('Name'), 'Mara Venn')
    await user.type(screen.getByLabelText('Slug'), 'taken')
    await user.click(screen.getByRole('button', { name: 'Create entity' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('slug is used by another entity')
    expect(screen.getByLabelText('Name')).toHaveValue('Mara Venn')
    expect(screen.getByLabelText('Slug')).toHaveValue('taken')
  })

  it('sends only the fields an edit changed', async () => {
    const original = entity()
    const api = mockApi({
      ...base,
      'PATCH /entities/ent_mara': entity({ name: 'Mara Marsh' }),
    })
    const user = userEvent.setup()
    const onSaved = vi.fn()
    const { container } = renderInProject(<EntityForm entity={original} onSaved={onSaved} />)

    expect(await screen.findByLabelText('Type')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing has changed')

    const name = screen.getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Mara Marsh')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(api.find('PATCH', '/entities/ent_mara')[0].body).toEqual({
      name: 'Mara Marsh',
      expected_revision: 3,
    })
    await expectAccessible(container)
  })
})
