/**
 * A draft's words as the model writes them (SCN-002). The studio opens the job's stream
 * while a draft is being written and shows the words as they arrive; the draft is still
 * stored whole on the server and appears among the drafts waiting once it is. If the
 * stream cannot be opened (an older server, a network that holds streams back, a model
 * on the author's own computer that answers all at once), nothing is shown and the
 * progress the studio already polls carries on as before.
 */
import { useEffect, useState } from 'react'
import { openJobStream, type Scope } from '../api/client'
import { readEvents } from './sse'

export interface DraftWords {
  /** Each draft's words so far, by its number in the job (1, 2, …). */
  texts: Record<number, string>
  /** Drafts stored whole: they are now among the drafts waiting. */
  stored: number[]
  /** True once any words have arrived. */
  streaming: boolean
  /** The newest stage and progress the stream reported (fresher than the polled job). */
  stage?: string
  progress?: number
}

const EMPTY: DraftWords = { texts: {}, stored: [], streaming: false }
// After a dropped connection, how long to wait before opening the stream again.
const RETRY_MS = 1500
const MAX_TRIES = 5
const AGAIN_MS = 250

/** Applies one `text` event: words from an offset (0 again when a draft starts over). */
export function withWords(current: DraftWords, number: number, offset: number, words: string): DraftWords {
  const before = current.texts[number] ?? ''
  const kept = offset >= before.length ? before : before.slice(0, Math.max(0, offset))
  return { ...current, texts: { ...current.texts, [number]: kept + words }, streaming: true }
}

function own(state: { jobId: string | null; value: DraftWords }, jobId: string): DraftWords {
  return state.jobId === jobId ? state.value : EMPTY
}

export function useDraftStream(
  scope: Scope,
  jobId: string | null,
  { onStored }: { onStored?: (number: number) => void } = {},
): DraftWords {
  const [words, setWords] = useState<{ jobId: string | null; value: DraftWords }>({ jobId, value: EMPTY })
  useEffect(() => {
    if (!jobId) return
    const control = new AbortController()
    let tries = 0
    let finished = false
    const run = async () => {
      while (!finished && !control.signal.aborted && tries < MAX_TRIES) {
        let again = false
        try {
          const response = await openJobStream(scope, jobId, control.signal)
          const type = response.headers.get('Content-Type') ?? ''
          if (!response.ok || !response.body || !type.startsWith('text/event-stream')) return
          tries = 0
          await readEvents(response.body, (event) => {
            let data: Record<string, unknown> = {}
            try {
              data = JSON.parse(event.data) as Record<string, unknown>
            } catch {
              return
            }
            if (event.event === 'text') {
              const number = Number(data.number)
              const offset = Number(data.offset)
              const text = typeof data.text === 'string' ? data.text : ''
              setWords((state) => ({ jobId, value: withWords(own(state, jobId), number, offset, text) }))
            } else if (event.event === 'draft_done') {
              const number = Number(data.number)
              setWords((state) => {
                const value = own(state, jobId)
                return { jobId, value: { ...value, stored: [...value.stored, number] } }
              })
              onStored?.(number)
            } else if (event.event === 'job') {
              const stage = typeof data.stage === 'string' ? data.stage : undefined
              const progress = Number(data.progress)
              setWords((state) => ({
                jobId,
                value: { ...own(state, jobId), stage, progress: Number.isFinite(progress) ? progress : undefined },
              }))
            } else if (event.event === 'end') {
              finished = true
            } else if (event.event === 'again') {
              again = true
            }
          })
        } catch {
          if (control.signal.aborted) return
        }
        if (finished || control.signal.aborted) return
        // A stream that lasted its time is opened again at once (well, nearly); one
        // that dropped is tried again a few times, then left to the polled progress.
        if (!again) tries += 1
        await new Promise((resolve) => setTimeout(resolve, again ? AGAIN_MS : RETRY_MS))
      }
    }
    void run()
    return () => {
      finished = true
      control.abort()
    }
    // onStored is read when an event arrives; a new function each render must not reopen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.workspaceId, scope.projectId, scope.branchId, jobId])
  return words.jobId === jobId ? words.value : EMPTY
}
