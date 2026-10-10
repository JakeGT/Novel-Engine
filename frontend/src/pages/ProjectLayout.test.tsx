import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity, mutation, projection, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, OWNER, problem, reply, VIEWER } from '../test/harness'
import { BiblePage } from './BiblePage'
import { ProjectLayout } from './ProjectLayout'

afterEach(() => vi.unstubAllGlobals())

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects/prj_1/bible']}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectLayout />}>
            <Route path="bible" element={<BiblePage />} />
            <Route path="entries/:entityId" element={<p>entry page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const session = { user: { id: 'usr_1', email: 'jake@example.com', display_name: 'Jake Talling' }, auth_mode: 'hosted' }
const published = [{ id: 'scv_1', version: 1, status: 'published', definition: {} }]
const pages = {
  'GET /auth/me': session,
  'GET /schemas': published,
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /entities': {
    items: [entity(), entity({ id: 'ent_h', type: 'Location', name: 'Lantern Harbor', aliases: [] })],
    limit: 200,
    offset: 0,
  },
  'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
  'GET /mutations': { items: [mutation(), mutation({ id: 'mut_2', status: 'committed' })], limit: 100, offset: 0 },
  'GET /projection': projection({ head_revision: 7 }),
  'GET /access': OWNER,
}

describe('ProjectLayout', () => {
  it('gives an author navigation, the saved version, and a count of what is waiting', async () => {
    mockApi(pages)
    const { container } = renderShell()

    const nav = await screen.findByRole('navigation', { name: 'Project' })
    expect(await within(nav).findByRole('link', { name: /Characters/ })).toHaveAttribute(
      'href',
      '/projects/prj_1/bible?type=Character',
    )
    // Only undecided changes are counted.
    expect(await within(nav).findByRole('link', { name: /Review/ })).toHaveTextContent('1 waiting')
    expect(await screen.findByText('Version 7 · saved')).toBeInTheDocument()
    expect(await screen.findByText('Jake Talling')).toBeInTheDocument()
    expect(await screen.findAllByRole('button', { name: 'New entry' })).not.toHaveLength(0)
    await expectAccessible(container)
  })

  it('adds an entry from anywhere and lands on it', async () => {
    const api = mockApi({ ...pages, 'POST /entities': reply(201, entity({ id: 'ent_new', name: 'Tobin' })) })
    const user = userEvent.setup()
    renderShell()

    await user.click((await screen.findAllByRole('button', { name: 'New entry' }))[0])
    const dialog = screen.getByRole('dialog', { name: 'Add to your story bible' })
    await user.type(within(dialog).getByLabelText('Name'), 'Tobin')
    await user.click(within(dialog).getByRole('button', { name: 'Add to story bible' }))

    expect(await screen.findByText('entry page')).toBeInTheDocument()
    expect(api.find('POST', '/entities')[0].body).toMatchObject({ type: 'Character', name: 'Tobin', expected_revision: 7 })
  })

  it('finds entries from the keyboard palette', async () => {
    mockApi({
      ...pages,
      'GET /entities/search': { items: [entity({ id: 'ent_h', type: 'Location', name: 'Lantern Harbor' })], limit: 12, offset: 0 },
    })
    const user = userEvent.setup()
    renderShell()

    await screen.findByText('Version 7 · saved')
    await user.keyboard('{Control>}k{/Control}')
    await user.type(screen.getByRole('combobox', { name: 'Search your story bible' }), 'lantern')
    await waitFor(() => expect(screen.getAllByRole('option')[0]).toHaveTextContent('Lantern Harbor'))
    await user.keyboard('{Enter}')
    expect(await screen.findByText('entry page')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('sets a brand-new project up for its owner without asking', async () => {
    let versions: unknown[] = []
    const api = mockApi({
      ...pages,
      'GET /schemas': () => versions,
      'POST /schemas/drafts': reply(201, { version: { id: 'scv_1', version: 1 }, compatibility: 'additive', migration_plan: [] }),
      'POST /schemas/scv_1/publish': () => {
        versions = published
        return published[0]
      },
    })
    renderShell()

    await waitFor(() => expect(api.find('POST', '/schemas/scv_1/publish')).toHaveLength(1))
    expect(api.find('POST', '/schemas/drafts')).toHaveLength(1)
    await waitFor(() => expect(screen.queryByText(/Getting your story bible ready/)).not.toBeInTheDocument())
  })

  it('turns writing off for a read-only member and never sets the project up for them', async () => {
    const api = mockApi({ ...pages, 'GET /access': VIEWER, 'GET /schemas': [] })
    renderShell()

    expect(await screen.findByText('Read-only access')).toBeInTheDocument()
    expect(await screen.findByText(/still being set up by its owner/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New entry' })).not.toBeInTheDocument()
    expect(api.find('POST', '/schemas/drafts')).toHaveLength(0)
  })

  it('never presents a failed restore as a healthy project', async () => {
    mockApi({
      ...pages,
      'GET /projection': projection({
        import: {
          id: 'imp_1',
          status: 'failed',
          stage: 'commits',
          commits_total: 7,
          commits_applied: 3,
          objects_imported: 1,
          error: 'RuntimeError',
        },
      }),
    })
    renderShell()

    const banner = await screen.findByText(/stopped part-way, so this project is incomplete/)
    expect(banner.closest('[role="alert"]')).toHaveTextContent('3 of 7 versions restored')
  })

  it('says plainly when the project is not available instead of showing an empty shell', async () => {
    mockApi({ ...pages, 'GET /access': problem(404, 'not_found', 'Project not found.') })
    renderShell()

    expect(await screen.findByText('This project isn’t available to you')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to your library' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('navigation', { name: 'Project' })).not.toBeInTheDocument()
  })
})
