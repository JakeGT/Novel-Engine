import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Items, type MutationValidation, type ReviewDismissAsk, type ReviewFinding } from '../api/client'
import { scopedKey, useProject } from '../project'

type Issue = MutationValidation['issues'][number]

const issueKey = (ordinal: number, code: string, message: string) => `${ordinal}␟${code}␟${message}`

/** The proposal's advisory findings with their dismissals, and the actions on them. */
export function useReviewFindings(mutationId: string, enabled: boolean, version: string) {
  const { scope } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'review-findings', mutationId, version)
  const found = useQuery({
    queryKey: key,
    queryFn: () => api.reviewFindings(scope, mutationId),
    enabled,
    retry: false,
  })
  const put = (next: Items<ReviewFinding>) => client.setQueryData(key, next)
  const dismiss = useMutation({
    mutationFn: (ask: ReviewDismissAsk) => api.dismissReviewFinding(scope, mutationId, ask),
    onSuccess: put,
  })
  const undo = useMutation({
    mutationFn: (dismissalId: string) => api.undoReviewDismissal(scope, mutationId, dismissalId),
    onSuccess: put,
  })
  const byIssue = new Map(
    (found.data?.items ?? []).map((item) => [issueKey(item.ordinal, item.code, item.message), item] as const),
  )
  return {
    find: (issue: Issue) => byIssue.get(issueKey(issue.ordinal, issue.code, issue.message)),
    dismiss,
    undo,
  }
}


export type ReviewFindings = ReturnType<typeof useReviewFindings>
