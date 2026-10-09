import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { projection, VOCABULARY } from '../test/fixtures'
import { expectAccessible, mockApi, OWNER, VIEWER } from '../test/harness'
import { EntityList } from './EntityList'
import { ProjectLayout } from './ProjectLayout'

afterEach(() => vi.unstubAllGlobals())

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects/prj_1']}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<EntityList />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const pages = {
  'GET /schemas/vocabulary': VOCABULARY,
  'GET /entities': { items: [], limit: 100, offset: 0 },
  'GET /entities/name-collisions': { items: [], limit: 0, offset: 0 },
  'GET /mutations': { items: [], limit: 100, offset: 0 },
}

describe('ProjectLayout', () => {
  it('shows head, index state, and role, and lets an editor write', async () => {
    mockApi({
      ...pages,
      'GET /projection': projection({ head_revision: 7, applied_revision: 5, lag: 2, current: false, status: 'lagging' }),
      'GET /access': OWNER,
    })
    const { container } = renderShell()

    expect(await screen.findByText('r7')).toBeInTheDocument()
    expect(screen.getByText(/Graph index 2 revisions behind \(canon is unaffected\)/)).toBeInTheDocument()
    expect(await screen.findByText('Your role: owner')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Create entity' })).toBeEnabled()
    expect(screen.getByRole('navigation', { name: 'Project' })).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('turns writing off for a read-only member and says why', async () => {
    mockApi({ ...pages, 'GET /projection': projection(), 'GET /access': VIEWER })
    renderShell()

    expect(await screen.findByText('Your role: viewer (read-only)')).toBeInTheDocument()
    expect(await screen.findByText(/You have read-only access to this project/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create entity' })).not.toBeInTheDocument()
  })

  it('never presents a failed archive import as a healthy project', async () => {
    mockApi({
      ...pages,
      'GET /access': OWNER,
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

    const banner = await screen.findByText(/An archive import into this project failed part-way/)
    expect(banner).toHaveTextContent('3 of 7 revisions restored')
    expect(banner.closest('[role="alert"]')).not.toBeNull()
  })

  it('shows no import banner once an import has completed', async () => {
    mockApi({
      ...pages,
      'GET /access': OWNER,
      'GET /projection': projection({
        import: {
          id: 'imp_1',
          status: 'completed',
          stage: 'verified',
          commits_total: 7,
          commits_applied: 7,
          objects_imported: 1,
          error: null,
        },
      }),
    })
    renderShell()
    expect(await screen.findByText('Graph index current')).toBeInTheDocument()
    expect(screen.queryByText(/archive import/i)).not.toBeInTheDocument()
  })
})
