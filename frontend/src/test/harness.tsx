import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import axe from 'axe-core'
import type { ReactElement } from 'react'
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { expect, vi } from 'vitest'
import type { ProjectAccess } from '../api/client'
import type { ProjectContext } from '../project'

export interface RecordedCall {
  method: string
  path: string
  search: URLSearchParams
  body: unknown
}

/** An explicit HTTP reply; anything else a handler returns is a 200 JSON body. */
export class Reply {
  status: number
  body: unknown

  constructor(status: number, body: unknown = {}) {
    this.status = status
    this.body = body
  }
}

export function reply(status: number, body: unknown = {}): Reply {
  return new Reply(status, body)
}

type Handler = (call: RecordedCall) => Reply | unknown

export interface MockApi {
  calls: RecordedCall[]
  /** Calls matching a method and path fragment, in order. */
  find: (method: string, fragment: string) => RecordedCall[]
}

export function problem(status: number, code: string, detail: string, fieldErrors = {}): Reply {
  return new Reply(status, {
    type: 'about:blank',
    title: code,
    status,
    code,
    detail,
    field_errors: fieldErrors,
  })
}

/**
 * Replace `fetch` with a router over "METHOD /path/fragment" keys. The longest
 * matching fragment wins; an unmatched request fails the test loudly as a 500.
 */
export function mockApi(routes: Record<string, Handler | Reply | unknown>): MockApi {
  const calls: RecordedCall[] = []
  const entries = Object.entries(routes).sort((a, b) => b[0].length - a[0].length)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost')
      const method = (init?.method ?? 'GET').toUpperCase()
      const call: RecordedCall = {
        method,
        path: url.pathname,
        search: url.searchParams,
        // JSON bodies are parsed for assertions; anything else (an uploaded file) is kept as sent.
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : (init?.body ?? undefined),
      }
      calls.push(call)
      const match = entries.find(([key]) => {
        const [keyMethod, fragment] = key.split(' ')
        return keyMethod === method && url.pathname.endsWith(fragment)
      })
      if (!match) {
        return new Response(
          JSON.stringify({ code: 'unmocked', detail: `unmocked ${method} ${url.pathname}` }),
          { status: 500, headers: { 'Content-Type': 'application/json' } },
        )
      }
      const value = typeof match[1] === 'function' ? (match[1] as Handler)(call) : match[1]
      const answer = value instanceof Reply ? value : new Reply(200, value)
      return new Response(JSON.stringify(answer.body ?? {}), {
        status: answer.status,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return {
    calls,
    find: (method, fragment) =>
      calls.filter((call) => call.method === method && call.path.endsWith(fragment)),
  }
}

export const OWNER: ProjectAccess = {
  role: 'owner',
  permissions: ['project.read', 'project.write', 'project.admin'],
  can_write: true,
  can_admin: true,
  auth_mode: 'hosted',
  hosted: true,
}

export const VIEWER: ProjectAccess = {
  role: 'viewer',
  permissions: ['project.read'],
  can_write: false,
  can_admin: false,
  auth_mode: 'hosted',
  hosted: true,
}

export function projectContext(overrides: Partial<ProjectContext> = {}): ProjectContext {
  const access = overrides.access ?? OWNER
  return {
    scope: { workspaceId: 'ws_1', projectId: 'prj_1' },
    headRevision: 3,
    refreshHead: vi.fn(async () => 5),
    access,
    canWrite: access.can_write,
    canAdmin: access.can_admin,
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

interface RenderOptions {
  context?: Partial<ProjectContext>
  /** Route pattern below /projects/:projectId, e.g. "mutations/:mutationId". */
  path?: string
  /** Concrete URL below /projects/prj_1, e.g. "mutations/mut_1". */
  at?: string
  state?: unknown
}

/** Render a project page the way ProjectLayout hosts it, with a fresh query cache. */
export function renderInProject(ui: ReactElement, options: RenderOptions = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const context = projectContext(options.context)
  const base = '/projects/prj_1'
  const entry = { pathname: options.at ? `${base}/${options.at}` : base, state: options.state }
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/projects/:projectId" element={<Outlet context={context} />}>
            {options.path ? <Route path={options.path} element={ui} /> : <Route index element={ui} />}
            <Route path="*" element={<LocationProbe />} />
          </Route>
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...result, context, client }
}

/**
 * Automated accessibility check. Colour contrast needs real layout, so it is
 * checked in the browser run (e2e) instead of jsdom.
 */
export async function expectAccessible(container: HTMLElement): Promise<void> {
  const results = await axe.run(container, {
    rules: { 'color-contrast': { enabled: false }, region: { enabled: false } },
  })
  expect(
    results.violations.map((violation) => `${violation.id}: ${violation.help}`),
  ).toEqual([])
}
