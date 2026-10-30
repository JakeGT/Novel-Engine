import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type PlanItem, type SceneSummaries, type SceneSummary } from '../api/client'
import { isFinished } from '../lib/studio'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, ErrorNotice } from './ui'

const JOB_EVERY = 1500

const ORIGIN: Record<string, string> = {
  author: 'Written by you',
  model: 'Suggested by the assistant and kept by you',
  model_corrected: 'Suggested by the assistant and corrected by you',
}

const words = (text: string) => text.trim().split(/\s+/).filter(Boolean).length

/**
 * The short summary later scenes are given of this one (CTX-006). The author writes it,
 * or asks the assistant to suggest one and keeps, corrects or turns it down: a
 * suggestion is never used until it is kept.
 */
export function SceneSummaryPanel({ scene }: { scene: PlanItem }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'scene-summary', scene.id)
  const summary = useQuery({ queryKey: key, queryFn: () => api.sceneSummary(scope, scene.id) })
  const [jobId, setJobId] = useState<string | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null)
  const job = useQuery({
    queryKey: scopedKey(scope, 'scene-summary-job', jobId),
    queryFn: () => api.job(scope, jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const done = isFinished(job.data)
  useEffect(() => {
    if (done) void client.invalidateQueries({ queryKey: key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done])
  const refresh = (next: SceneSummaries) => {
    client.setQueryData(key, next)
    // Later scenes' contexts read the kept summary.
    void client.invalidateQueries({ queryKey: scopedKey(scope, 'scene-context') })
  }
  const write = useMutation({
    mutationFn: (body: string) => api.writeSceneSummary(scope, scene.id, body),
    onSuccess: (next) => {
      setDraft(null)
      refresh(next)
    },
  })
  const suggest = useMutation({
    mutationFn: () => api.suggestSceneSummary(scope, scene.id),
    onSuccess: (started) => setJobId(started.id),
  })
  const decide = useMutation({
    mutationFn: ({ item, accept, body }: { item: SceneSummary; accept: boolean; body?: string }) =>
      api.decideSceneSummary(scope, scene.id, item.id, accept, body),
    onSuccess: (next) => {
      setEditing(null)
      refresh(next)
    },
  })

  const data = summary.data
  const running = Boolean(job.data && !isFinished(job.data))
  const failed = job.data?.status === 'failed'
  const busy = write.isPending || suggest.isPending || decide.isPending || running

  return (
    <section className="stack tight scene-summary" aria-labelledby={`summary-${scene.id}`}>
      <h3 id={`summary-${scene.id}`}>Summary for later scenes</h3>
      <ErrorNotice error={summary.error} prefix="Could not find this scene’s summary" />
      {data && !data.has_prose && (
        <p className="muted small">
          When this scene is written, you can keep a short summary of it here. Until then, later
          scenes are given what its plan card says.
        </p>
      )}
      {data && data.has_prose && (
        <>
          {data.kept ? (
            <div className="summary-kept">
              <p className="summary-body">{data.kept.body}</p>
              <p className="muted small">
                {ORIGIN[data.kept.origin] ?? 'Kept by you'}
                {data.kept_out_of_date && ' · '}
                {data.kept_out_of_date && (
                  <strong className="summary-stale">
                    Out of date: the scene has been rewritten since. Later scenes still get it, ranked
                    lower, until you summarise it again.
                  </strong>
                )}
              </p>
            </div>
          ) : (
            <p className="muted small">
              No summary kept yet. Later scenes are given what the plan card says
              {data.plan_card ? `: “${data.plan_card.replace(/[.!?]+$/, '')}”` : ', and the plan card says little'}.
            </p>
          )}

          {(data.suggested ?? []).map((item) =>
            editing?.id === item.id ? (
              <div className="summary-suggested stack tight" key={item.id}>
                <label className="field">
                  <span>Correct the suggestion, then keep it</span>
                  <textarea
                    rows={4}
                    value={editing.body}
                    onChange={(event) => setEditing({ id: item.id, body: event.target.value })}
                  />
                </label>
                <span className="muted small">
                  {plural(words(editing.body), 'word')} of {data.max_words} at most
                </span>
                <div className="actions">
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={busy || words(editing.body) === 0}
                    onClick={() => decide.mutate({ item, accept: true, body: editing.body })}
                  >
                    Keep my correction
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>
                    Stop correcting
                  </Button>
                </div>
              </div>
            ) : (
              <div className="summary-suggested stack tight" key={item.id}>
                <p className="label">The assistant suggests</p>
                <p className="summary-body">{item.body}</p>
                <p className="muted small">Not used until you keep it.</p>
                {canWrite && (
                  <div className="actions">
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={busy}
                      onClick={() => decide.mutate({ item, accept: true })}
                    >
                      Keep this summary
                    </Button>
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => setEditing({ id: item.id, body: item.body })}
                    >
                      Correct it
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => decide.mutate({ item, accept: false })}
                    >
                      Turn it down
                    </Button>
                  </div>
                )}
              </div>
            ),
          )}

          {canWrite && draft !== null && (
            <div className="stack tight">
              <label className="field">
                <span>Your summary of the scene as it is written now</span>
                <textarea rows={4} value={draft} onChange={(event) => setDraft(event.target.value)} />
              </label>
              <span className="muted small">
                {plural(words(draft), 'word')} of {data.max_words} at most. Later scenes are given it,
                so a short one leaves room for more.
              </span>
              <div className="actions">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || words(draft) === 0}
                  onClick={() => write.mutate(draft)}
                >
                  {write.isPending ? 'Keeping…' : 'Keep my summary'}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDraft(null)}>
                  Stop writing
                </Button>
              </div>
            </div>
          )}

          {canWrite && draft === null && (
            <div className="actions">
              <Button size="sm" disabled={busy} onClick={() => setDraft(data.kept?.body ?? '')}>
                {data.kept ? 'Rewrite the summary' : 'Write a summary'}
              </Button>
              <Button size="sm" disabled={busy} onClick={() => suggest.mutate()}>
                {running || suggest.isPending ? 'The assistant is reading the scene…' : 'Ask the assistant to suggest one'}
              </Button>
            </div>
          )}
          {failed && (
            <p className="notice small" role="status">
              The assistant could not suggest a summary: {job.data?.error_message ?? 'something went wrong'}.
            </p>
          )}
          <ErrorNotice error={write.error ?? suggest.error ?? decide.error} />
        </>
      )}
    </section>
  )
}
