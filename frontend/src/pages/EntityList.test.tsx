import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectContext } from './ProjectLayout'
import { EntityList } from './EntityList'

function renderEntityList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const context: ProjectContext = {
    scope: { workspaceId: 'ws_1', projectId: 'prj_1' },
    headRevision: 3,
  }
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects/prj_1']}>
        <Routes>
          <Route path="/projects/:projectId" element={<Outlet context={context} />}>
            <Route index element={<EntityList />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('EntityList', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            items: [
              {
                id: 'ent_1',
                type: 'Character',
                name: 'Mara',
                lifecycle: 'active',
                schema_version_id: 'scv_1',
                created_revision: 1,
              },
            ],
            limit: 20,
            offset: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
  })

  it('renders project-scoped entities from the API', async () => {
    renderEntityList()
    expect(await screen.findByText('Mara')).toBeTruthy()
    const call = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls[0]
    expect(String(call[0])).toContain('/api/v1/projects/prj_1/entities')
  })
})
