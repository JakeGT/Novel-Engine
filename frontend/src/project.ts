import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { isStaleHead, type ProjectAccess, type Scope } from './api/client'

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
}

export function useProject(): ProjectContext {
  return useOutletContext<ProjectContext>()
}

/** Every cache key starts with workspace + project so one scope can never serve another. */
export function scopedKey(scope: Scope, ...parts: unknown[]): unknown[] {
  return [parts[0], scope.workspaceId, scope.projectId, ...parts.slice(1)]
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
  onSuccess?: (result: T) => void,
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
      onSuccess?.(result)
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
