import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type PartSummaries, type PartSummary, type PlanItem } from '../api/client'
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
 * The short summary scenes after a whole beat or arc are given of it (CTX-006). Until the
 * author keeps one, it is worked out from the summaries kept for its scenes. The author
 * writes one, or asks the assistant to suggest one from those summaries and keeps,
 * corrects or turns it down: a suggestion is never used until it is kept.
 */
export function PartSummaryPanel({ part }: { part: PlanItem }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'part-summary', part.id)
  const summary = useQuery({ queryKey: key, queryFn: () => api.partSummary(scope, part.id) })
  const [jobId, setJobId] = useState<string | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null)
  const job = useQuery({
    queryKey: scopedKey(scope, 'part-summary-job', jobId),
    queryFn: () => api.job(scope, jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const done = isFinished(job.data)
  useEffect(() => {
    if (done) void client.invalidateQueries({ queryKey: key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done])
  const refresh = (next: PartSummaries) => {
    client.setQueryData(key, next)
    // Later scenes' contexts read the kept summary.
    void client.invalidateQueries({ queryKey: scopedKey(scope, 'scene-context') })
  }
  const write = useMutation({
    mutationFn: (body: string) => api.writePartSummary(scope, part.id, body),
    onSuccess: (next) => {
      setDraft(null)
      refresh(next)
    },
  })
  const suggest = useMutation({
    mutationFn: () => api.suggestPartSummary(scope, part.id),
    onSuccess: (started) => setJobId(started.id),
  })
  const decide = useMutation({
    mutationFn: ({ item, accept, body }: { item: PartSummary; accept: boolean; body?: string }) =>
      api.decidePartSummary(scope, part.id, item.id, accept, body),
    onSuccess: (next) => {
      setEditing(null)
      refresh(next)
    },
  })

  const data = summary.data
  const kind = data?.kind ?? part.kind
  const running = Boolean(job.data && !isFinished(job.data))
  const failed = job.data?.status === 'failed'
  const busy = write.isPending || suggest.isPending || decide.isPending || running
  const headingId = `part-summary-${part.id}`
  const missing = data?.unsummarised ?? []

  return (
    <section className="stack tight scene-summary" aria-labelledby={headingId}>
      <h3 id={headingId}>
        Summary of the {kind} “{part.title}”
      </h3>
      <p className="muted small">Scenes after this {kind} are given this summary of it.</p>
      <ErrorNotice error={summary.error} prefix={`Could not find this ${kind}’s summary`} />
      {data && (
        <>
          {data.kept ? (
            <div className="summary-kept">
              <p className="summary-body">{data.kept.body}</p>
              <p className="muted small">
                {ORIGIN[data.kept.origin] ?? 'Kept by you'}
                {data.kept_out_of_date && ' · '}
                {data.kept_out_of_date && (
                  <strong className="summary-stale">
                    Out of date: a scene summary in it has changed since. Later scenes still get
                    it, ranked lower, until you summarise it again.
                  </strong>
                )}
              </p>
            </div>
          ) : data.derived ? (
            <div className="summary-kept">
              <p className="summary-body">{data.derived}</p>
              <p className="muted small">
                Worked out from the summaries you kept of its scenes. Keep your own to say it in
                fewer words.
              </p>
            </div>
          ) : (
            <p className="muted small">
              Nothing to give yet: none of its {plural(data.scenes, 'scene')} has a kept summary.
              Keep a summary of a scene, or write one for the whole {kind} here.
            </p>
          )}
          {missing.length > 0 && (data.kept || data.derived) && (
            <p className="muted small">
              Not covered, because they have no kept summary: {missing.join(', ')}.
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
                <p className="muted small">
                  From {plural(item.scenes, 'scene summary', 'scene summaries')}. Not used until you
                  keep it.
                </p>
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
                <span>Your summary of the whole {kind}</span>
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
              <Button size="sm" disabled={busy} onClick={() => setDraft(data.kept?.body ?? data.derived ?? '')}>
                {data.kept ? 'Rewrite the summary' : `Write a summary of the ${kind}`}
              </Button>
              {data.derived && (
                <Button size="sm" disabled={busy} onClick={() => suggest.mutate()}>
                  {running || suggest.isPending
                    ? 'The assistant is reading the summaries…'
                    : 'Ask the assistant to suggest one'}
                </Button>
              )}
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
