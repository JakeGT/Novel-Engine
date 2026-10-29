import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { isFinished, tokenCount, type Span } from '../lib/studio'
import { plural } from '../lib/words'
import { scopedKey, useInvalidateProject, useProject } from '../project'
import type { Point } from './StudioFindings'
import { JobProgress } from './StudioWork'
import { Button, ErrorNotice, Loading } from './ui'

const JOB_EVERY = 1500

/** The first few words of a passage, to say which one it is. */
function opening(text: string, span: Span | null | undefined): string {
  if (!span) return 'A passage'
  const words = text.slice(span.start, span.end).split(/\s+/).filter(Boolean)
  return words.length > 8 ? `${words.slice(0, 8).join(' ')}…` : words.join(' ')
}

/**
 * When the measures point a long way from the author's usual, a small check with the
 * assistant on just the passages that show it (CON-018). Offered only then; the passages
 * and the cost are shown before anything is sent, and its notes, which quote the words,
 * join the assistant's other notes on the scene.
 */
export function AskAboutVoice({
  planId,
  versionId,
  notesKey,
  text,
  point,
  onShow,
}: {
  planId: string
  versionId: string | null
  /** Changes when the notes do (one dismissed, say), so the offer is worked out again. */
  notesKey: string
  text: string
  point: Point
  onShow: (span: Span) => void
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const invalidate = useInvalidateProject(scope)
  const [open, setOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const offer = useQuery({
    queryKey: scopedKey(scope, 'voice-escalation', planId, versionId ?? '', notesKey),
    queryFn: () => api.voiceEscalation(scope, planId),
    enabled: Boolean(versionId),
    retry: false,
  })
  const job = useQuery({
    queryKey: scopedKey(scope, 'voice-escalation-job', jobId),
    queryFn: () => api.job(scope, jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data) ? false : JOB_EVERY),
  })
  const done = isFinished(job.data)
  const refreshed = useRef<string | null>(null)
  useEffect(() => {
    if (done && jobId && refreshed.current !== jobId) {
      refreshed.current = jobId
      void invalidate()
    }
  }, [done, jobId, invalidate])
  const begin = useMutation({
    mutationFn: () => api.startVoiceEscalation(scope, planId),
    onSuccess: (started) => setJobId(started.id),
  })
  const stop = useMutation({
    mutationFn: () => api.cancelJob(scope, jobId as string),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'voice-escalation-job', jobId) }),
  })
  const found = offer.data
  if (!found?.offered && !jobId) return null

  if (!open && !jobId) {
    return (
      <p className="small">
        These notes point a long way from your usual.{' '}
        <button type="button" className="link-button" onClick={() => setOpen(true)}>
          Ask the assistant about the passages that show it
        </button>
      </p>
    )
  }
  const running = job.data && !isFinished(job.data)
  const kept = job.data?.status === 'succeeded' ? Number(job.data.result.findings ?? 0) : 0
  return (
    <div className="plan-help stack tight" role="group" aria-label="Ask the assistant how these passages sound">
      {!jobId && found && (
        <>
          <p className="small">
            The assistant reads only {plural(found.passages.length, 'passage')} ({plural(found.words, 'word')}), why they
            stand out, and your notes on how you write. About {tokenCount(found.total_tokens)} tokens. Its notes quote
            the words and change nothing.
          </p>
          <ul className="voice-flagged small">
            {found.passages.map((passage) => {
              const span = point(passage.start, passage.end)
              return (
                <li key={`${passage.start}-${passage.end}`}>
                  “{opening(text, span)}”
                  {span && (
                    <>
                      {' '}
                      <button type="button" className="link-button" onClick={() => onShow(span)}>
                        Show in the text
                      </button>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
          <ErrorNotice error={begin.error} prefix="Could not ask the assistant" />
          <div className="actions">
            <Button size="sm" variant="primary" icon="spark" disabled={begin.isPending} onClick={() => begin.mutate()}>
              {begin.isPending ? 'Starting…' : 'Ask the assistant'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Not now
            </Button>
          </div>
        </>
      )}
      {jobId && !job.data && <Loading>Starting…</Loading>}
      {job.data && running && (
        <JobProgress job={job.data} stopping={stop.isPending} error={stop.error} onStop={() => stop.mutate()} />
      )}
      {job.data?.status === 'failed' && (
        <p className="notice warn small" role="alert">
          It did not finish: {job.data.error_message ?? 'something went wrong.'}
        </p>
      )}
      {job.data?.status === 'cancelled' && <p className="small" role="status">Stopped. Nothing was kept.</p>}
      {job.data?.status === 'succeeded' && (
        <p className="small" role="status">
          {kept > 0
            ? `Done: ${plural(kept, 'note')} from the assistant, with its other notes on this scene.`
            : 'Done: the assistant found nothing in these passages that does not sound like you.'}
        </p>
      )}
    </div>
  )
}
