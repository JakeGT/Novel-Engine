import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  api,
  isStaleHead,
  type AssistSession,
  type CanonEntity,
  type ProjectAccess,
  type Scope,
} from './api/client'

/** Everything a project page needs; provided by ProjectLayout through the router outlet. */
export interface ProjectContext {
  scope: Scope
  /** The canonical head this view last loaded. Writes are sent against it. */
  headRevision: number
  /** Re-read the canonical head from the server and return it. */
  refreshHead: () => Promise<number>
  access?: ProjectAccess
  canWrite: boolean
  canAdmin: boolean
  /** The signed-in user's id, so their own actions can read as "you". */
  userId?: string | null
  /** Open the "new entry" dialog, optionally with a type already chosen. */
  newEntry?: (type?: string) => void
  /** Open the assistant to fill in an entry that already exists. */
  fillIn?: (entity: CanonEntity) => void
  /** Open the God Hand, optionally focused on the entry being looked at. */
  godHand?: (entity?: CanonEntity) => void
  /** Reopen an unfinished conversation with the assistant where it was left. */
  resume?: (session: AssistSession) => void
}

const ProjectReactContext = createContext<ProjectContext | null>(null)

/** Lets pieces rendered outside the page outlet (dialogs, the search palette) use the project. */
export const ProjectProvider = ProjectReactContext.Provider

export function useProject(): ProjectContext {
  const provided = useContext(ProjectReactContext)
  const outlet = useOutletContext<ProjectContext | undefined>()
  return (provided ?? outlet) as ProjectContext
}

/**
 * Every cache key starts with workspace + project so one scope can never serve another,
 * then the what-if, so the story and a what-if never share an answer.
 */
export function scopedKey(scope: Scope, ...parts: unknown[]): unknown[] {
  return [parts[0], scope.workspaceId, scope.projectId, scope.branchId ?? 'main', ...parts.slice(1)]
}

/** The most entries one screen loads at once; lists say so when they reach it. */
export const ENTRY_LIMIT = 200

export interface Entries {
  items: CanonEntity[]
  /** Entry id → name, including archived entries, for writing facts as sentences. */
  names: Map<string, string>
  byId: Map<string, CanonEntity>
  loading: boolean
  error: unknown
  /** True when the project holds more entries than were loaded. */
  truncated: boolean
}

/** Every entry in the project (archived included), shared by all the screens that name entries. */
export function useEntries(scope: Scope): Entries {
  const query = useQuery({
    queryKey: scopedKey(scope, 'entities', 'all'),
    queryFn: () => api.listEntities(scope, { include_retired: true, limit: ENTRY_LIMIT }),
  })
  const items = query.data?.items ?? []
  return {
    items,
    names: new Map(items.map((item) => [item.id, item.name])),
    byId: new Map(items.map((item) => [item.id, item])),
    loading: query.isPending,
    error: query.error,
    truncated: items.length >= ENTRY_LIMIT,
  }
}

export function entryPath(scope: Scope, entityId: string): string {
  return `/projects/${scope.projectId}/entries/${entityId}`
}

/** "you" for the signed-in user; otherwise a neutral word, never a raw account id. */
export function actorLabel(actorId: string | null | undefined, userId: string | null | undefined): string {
  if (!actorId) return ''
  return actorId === userId ? 'you' : 'a collaborator'
}

export function useInvalidateProject(scope: Scope): () => Promise<void> {
  const queryClient = useQueryClient()
  return useCallback(
    () =>
      queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[1] === scope.workspaceId && query.queryKey[2] === scope.projectId,
      }),
    [queryClient, scope.workspaceId, scope.projectId],
  )
}

export interface CanonWrite<T, A = void> {
  /** Send the write against the head this view loaded. */
  submit: (argument: A) => Promise<T | undefined>
  /** Recovery path for a stale head: re-read the head, then resend the same entered data. */
  retryWithFreshHead: () => Promise<T | undefined>
  pending: boolean
  error: unknown
  stale: boolean
  reset: () => void
}

/**
 * Run a canon write with optimistic concurrency.
 *
 * The caller's form state is never touched here, so a stale-head or validation
 * failure leaves everything the author typed in place; `retryWithFreshHead`
 * resubmits it against the current head.
 */
export function useCanonWrite<T, A = void>(
  write: (expectedRevision: number, argument: A) => Promise<T>,
  onSuccess?: (result: T, argument: A) => void,
): CanonWrite<T, A> {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const lastArgument = useRef<A | undefined>(undefined)

  const run = async (resolveHead: () => Promise<number>, argument: A) => {
    lastArgument.current = argument
    setPending(true)
    setError(null)
    try {
      const result = await write(await resolveHead(), argument)
      await invalidate()
      onSuccess?.(result, argument)
      return result
    } catch (failure) {
      setError(failure)
      return undefined
    } finally {
      setPending(false)
    }
  }

  return {
    submit: (argument: A) => run(async () => headRevision, argument),
    retryWithFreshHead: () => run(refreshHead, lastArgument.current as A),
    pending,
    error,
    stale: isStaleHead(error),
    reset: () => setError(null),
  }
}
