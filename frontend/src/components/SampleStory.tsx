import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { uniqueSlug } from '../lib/identifiers'
import { useInvalidateProject, useProject } from '../project'
import { Button, ErrorNotice } from './ui'

const TITLE = 'Sample: The Kestrel Light'

/**
 * A short sample story to look around in before starting one's own. On a hosted
 * site it gets a project of its own, so it never mixes with the author's story;
 * a single-project install loads it into the (empty) project it has.
 */
export function SampleStory() {
  const { scope, access, canAdmin, headRevision } = useProject()
  const invalidate = useInvalidateProject(scope)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const separate = Boolean(access?.hosted && scope.workspaceId)

  const load = useMutation({
    mutationFn: async () => {
      if (!separate) {
        await api.loadSample(scope, headRevision)
        return scope.projectId
      }
      const workspaceId = scope.workspaceId as string
      const project = await api.createProject(workspaceId, { name: TITLE, slug: uniqueSlug('sample') })
      await api.loadSample({ workspaceId, projectId: project.id }, 0)
      return project.id
    },
    onSuccess: async (projectId) => {
      await queryClient.invalidateQueries({ queryKey: ['projects', scope.workspaceId] })
      await invalidate()
      if (projectId !== scope.projectId) navigate(`/projects/${projectId}`)
    },
  })

  // A separate project can only be made by someone who may create projects.
  if (separate && !canAdmin) return null

  return (
    <section className="card quiet" aria-labelledby="sample-heading">
      <div className="card-head">
        <div>
          <h2 id="sample-heading">Just looking?</h2>
          <p className="muted">
            Open a short sample story to see how a filled-in story bible reads: eight entries, how
            they are connected, one still to fill in, and a belief that turns out to be wrong.{' '}
            {separate
              ? 'It opens as a project of its own, so it never mixes with your story.'
              : 'It is added to this project, which has nothing in it yet.'}
          </p>
        </div>
      </div>
      <ErrorNotice error={load.error} prefix="The sample story was not opened" />
      <div>
        <Button icon="book" disabled={load.isPending} onClick={() => load.mutate()}>
          {load.isPending ? 'Opening…' : 'Open the sample story'}
        </Button>
      </div>
    </section>
  )
}
