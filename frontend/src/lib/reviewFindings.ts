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
  const recount = () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'review-calibration') })
  const dismiss = useMutation({
    mutationFn: (ask: ReviewDismissAsk) => api.dismissReviewFinding(scope, mutationId, ask),
    onSuccess: (next) => {
      put(next)
      recount()
    },
  })
  const undo = useMutation({
    mutationFn: (dismissalId: string) => api.undoReviewDismissal(scope, mutationId, dismissalId),
    onSuccess: (next) => {
      put(next)
      recount()
    },
  })
  // How often each kind is dismissed in this story: a note beside each, nothing more.
  const calibration = useQuery({
    queryKey: scopedKey(scope, 'review-calibration', version),
    queryFn: () => api.reviewCalibration(scope),
    enabled,
    retry: false,
  })
  const byKind = new Map((calibration.data?.items ?? []).map((line) => [line.code, line] as const))
  const byIssue = new Map(
    (found.data?.items ?? []).map((item) => [issueKey(item.ordinal, item.code, item.message), item] as const),
  )
  return {
    find: (issue: Issue) => byIssue.get(issueKey(issue.ordinal, issue.code, issue.message)),
    /** How often this kind of finding was dismissed here, once it has been dismissed at all. */
    calibration: (issue: Issue) => {
      const line = byKind.get(issue.code.startsWith('rule:') ? 'rule' : issue.code)
      return line && line.dismissed + line.accepted > 0 ? line.note : null
    },
    dismiss,
    undo,
  }
}


export type ReviewFindings = ReturnType<typeof useReviewFindings>
