import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useInvalidateProject, useProject } from '../project'
import { AllowanceNote } from './Allowance'
import { AssistantProblem } from './AssistFlow'
import { Button } from './ui'

/**
 * Asks the assistant for connections the entries already describe but no fact
 * records. What it finds is a set of suggested facts to review, never a change.
 */
export function FindConnections() {
  const { scope, headRevision, refreshHead } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const find = useMutation({
    mutationFn: async () => {
      // A proposal is not a canon write: aim it at whatever the latest version is right now.
      const head = await refreshHead().catch(() => headRevision)
      return api.assistLinks(scope, head)
    },
    onSuccess: async (result) => {
      await invalidate()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`, {
        state: {
          fromAssistant: true,
          understood: result.understood,
          notes: result.notes,
          discarded: result.discarded,
        },
      })
    },
  })

  return (
    <section className="card quiet" aria-labelledby="connections-heading">
      <div className="card-head">
        <div>
          <h2 id="connections-heading">Missing connections</h2>
          <p className="muted">
            Your entries often say how they are connected in their details, without a fact that
            records it. The assistant can read through them and suggest the facts that are
            missing. You review each one; nothing is added without you.
          </p>
        </div>
      </div>
      <AllowanceNote reads="story" />
      <AssistantProblem error={find.error} />
      <div>
        <Button icon="link" disabled={find.isPending} onClick={() => find.mutate()}>
          {find.isPending ? 'Reading your story bible…' : 'Find missing connections'}
        </Button>
      </div>
    </section>
  )
}
