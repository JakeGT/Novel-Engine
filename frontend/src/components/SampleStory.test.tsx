import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, OWNER, renderInProject, reply, VIEWER } from '../test/harness'
import { SampleStory } from './SampleStory'

afterEach(() => vi.unstubAllGlobals())

describe('SampleStory', () => {
  it('opens the sample as a project of its own on a hosted site', async () => {
    const api = mockApi({
      'POST /workspaces/ws_1/projects': reply(201, { id: 'prj_sample', name: 'Sample', slug: 'sample-x' }),
      'POST /entities/sample': reply(201, { revision: 1 }),
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SampleStory />)

    expect(screen.getByText(/It opens as a project of its own/)).toBeInTheDocument()
    await expectAccessible(container)
    await user.click(screen.getByRole('button', { name: 'Open the sample story' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_sample'),
    )
    expect(api.find('POST', '/projects')[0].body).toMatchObject({ name: 'Sample: The Kestrel Light' })
    // The sample goes into the new project, at its first version.
    expect(api.find('POST', '/entities/sample')[0].path).toContain('/projects/prj_sample/')
    expect(api.find('POST', '/entities/sample')[0].body).toEqual({ expected_revision: 0 })
  })

  it('loads into the one project a single-project install has', async () => {
    const api = mockApi({ 'POST /entities/sample': reply(201, { revision: 4 }) })
    const user = userEvent.setup()
    renderInProject(<SampleStory />, {
      context: {
        scope: { workspaceId: null, projectId: 'prj_1' },
        access: { ...OWNER, hosted: false, auth_mode: 'local' },
      },
    })

    expect(screen.getByText(/It is added to this project/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open the sample story' }))
    await waitFor(() => expect(api.find('POST', '/entities/sample')).toHaveLength(1))
    expect(api.find('POST', '/entities/sample')[0].body).toEqual({ expected_revision: 3 })
    expect(api.find('POST', '/projects')).toHaveLength(0)
  })

  it('is not offered to someone who cannot make a project', () => {
    mockApi({})
    renderInProject(<SampleStory />, { context: { access: { ...VIEWER, can_write: true } } })
    expect(screen.queryByRole('button', { name: 'Open the sample story' })).not.toBeInTheDocument()
  })
})
