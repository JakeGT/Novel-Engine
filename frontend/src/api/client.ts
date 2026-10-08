import type { components } from './schema'

export type CanonEntity = components['schemas']['CanonEntityModel']
export type Workspace = components['schemas']['WorkspaceModel']
export type Project = components['schemas']['ProjectModel']

export interface Scope {
  workspaceId: string | null
  projectId: string
}

export interface EntityListResponse {
  items: CanonEntity[]
  limit: number
  offset: number
}

export interface HistoryEntry {
  record_type: string
  record_id: string
  branch_id: string
  revision: number
  operation_type: string
  commit_id: string
  change: Record<string, unknown>
  created_at: string
}

export interface ProjectionStatus {
  projection: string
  branch_id: string
  head_revision: number
  applied_revision: number
  lag: number
  status: string
  current: boolean
  last_error: string | null
  failed_deliveries: number
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

const CSRF_COOKIE = 'novel_csrf'

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : ''
}

interface RequestOptions extends RequestInit {
  workspaceId?: string | null
  json?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { workspaceId, json, ...rest } = options
  const method = (rest.method ?? 'GET').toUpperCase()
  const headers = new Headers(rest.headers)
  headers.set('Accept', 'application/json')
  if (workspaceId) headers.set('X-Workspace-Id', workspaceId)
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  if (method !== 'GET' && method !== 'HEAD') headers.set('X-CSRF-Token', readCookie(CSRF_COOKIE))

  const response = await fetch(path, {
    credentials: 'include',
    ...rest,
    method,
    headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  session: () =>
    request<{ user: { id: string; email: string | null; display_name: string | null } }>(
      '/auth/me',
    ),

  listWorkspaces: () => request<Workspace[]>('/api/v1/workspaces'),

  listProjects: (workspaceId: string) =>
    request<Project[]>(`/api/v1/workspaces/${workspaceId}/projects`, { workspaceId }),

  listEntities: (
    scope: Scope,
    params: { limit?: number; offset?: number; include_retired?: boolean; entity_type?: string } = {},
  ) =>
    request<EntityListResponse>(
      `/api/v1/projects/${scope.projectId}/entities${query(params)}`,
      { workspaceId: scope.workspaceId },
    ),

  getEntity: (scope: Scope, entityId: string) =>
    request<CanonEntity>(`/api/v1/projects/${scope.projectId}/entities/${entityId}`, {
      workspaceId: scope.workspaceId,
    }),

  createEntity: (
    scope: Scope,
    body: { type: string; name: string; slug?: string; attributes?: Record<string, unknown>; expected_revision: number },
  ) =>
    request<CanonEntity>(`/api/v1/projects/${scope.projectId}/entities`, {
      method: 'POST',
      workspaceId: scope.workspaceId,
      json: body,
    }),

  retireEntity: (scope: Scope, entityId: string, expectedRevision: number) =>
    request<CanonEntity>(`/api/v1/projects/${scope.projectId}/entities/${entityId}/retire`, {
      method: 'POST',
      workspaceId: scope.workspaceId,
      json: { expected_revision: expectedRevision },
    }),

  entityHistory: (scope: Scope, entityId: string) =>
    request<{ items: HistoryEntry[] }>(
      `/api/v1/projects/${scope.projectId}/entities/${entityId}/history`,
      { workspaceId: scope.workspaceId },
    ),

  projection: (scope: Scope) =>
    request<ProjectionStatus>(`/api/v1/projects/${scope.projectId}/projection`, {
      workspaceId: scope.workspaceId,
    }),
}
