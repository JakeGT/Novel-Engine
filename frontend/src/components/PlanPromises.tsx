import type { PlanItem, PlanPromise } from '../api/client'
import { plural } from '../lib/words'

/** The reader waits this many scenes, in plain words. */
function waited(scenes: number): string {
  if (scenes === 0) return 'in the same scene'
  return `${plural(scenes, 'scene')} later`
}

/**
 * What the plan has promised the reader (STY-007): every thread opened and every
 * fact hinted, in the order the reader meets them. Promises still owed come first,
 * then those kept, each with how long the reader waits (counted in scenes).
 */
export function PlanPromises({
  promises,
  byId,
  clueText,
}: {
  promises: PlanPromise[]
  byId: Map<string, PlanItem>
  clueText: (factId: string) => string
}) {
  const owed = promises.filter((promise) => !promise.kept_in || promise.out_of_order)
  const kept = promises.filter((promise) => promise.kept_in && !promise.out_of_order)
  const place = (planId: string, at?: number | null) => {
    const title = byId.get(planId)?.title ?? 'a part of the plan'
    return (
      <>
        <a href={`#plan-${planId}`}>{title}</a>
        {at ? <span className="muted"> (scene {at})</span> : null}
      </>
    )
  }
  const what = (promise: PlanPromise) =>
    promise.kind === 'thread' ? (
      <strong>{byId.get(promise.subject_id)?.title ?? 'A thread'}</strong>
    ) : (
      <>
        <span className="promise-kind">Clue</span> <strong>{clueText(promise.subject_id)}</strong>
      </>
    )
  const row = (promise: PlanPromise) => (
    <li key={`${promise.kind}:${promise.subject_id}`} className={`promise ${promise.kept_in && !promise.out_of_order ? 'kept' : 'owed'}`}>
      <p className="promise-what">{what(promise)}</p>
      <p className="promise-path small">
        <span>
          {promise.kind === 'thread' ? 'Opened in ' : 'Hinted in '}
          {place(promise.made_in, promise.made_at)}
        </span>
        {promise.moved_along.length > 0 && (
          <span>
            {promise.kind === 'thread' ? 'Moved along in ' : 'Hinted again in '}
            {promise.moved_along.map((planId, index) => (
              <span key={planId}>
                {index > 0 && ', '}
                {place(planId)}
              </span>
            ))}
          </span>
        )}
        {promise.out_of_order && promise.kept_in ? (
          <span className="promise-status">Resolved in {place(promise.kept_in, promise.kept_at)}, before it is opened</span>
        ) : promise.kept_in ? (
          <span className="promise-status">
            {promise.kind === 'thread' ? 'Resolved in ' : 'Shown plainly in '}
            {place(promise.kept_in, promise.kept_at)} — {waited(promise.distance)}
          </span>
        ) : (
          <span className="promise-status">
            Still owed
            {promise.distance > 0 ? ` — ${plural(promise.distance, 'scene')} since, to the end of the plan` : ' — made in the last scene'}
          </span>
        )}
      </p>
    </li>
  )

  return (
    <section aria-labelledby="promises-heading" className="stack">
      <div>
        <h2 id="promises-heading">Promises to the reader</h2>
        <p className="muted small">
          Each thread you open and each clue you drop is something the reader waits for. Here is where each is
          kept, and how many scenes the reader waits.
        </p>
      </div>
      {promises.length === 0 ? (
        <p className="muted">
          No promises yet. Open a thread from a scene, or hint at a fact in “What the reader learns”, and it is
          listed here.
        </p>
      ) : (
        <>
          <div className="stack tight">
            <h3>{owed.length === 0 ? 'Nothing still owed' : `Still owed (${owed.length})`}</h3>
            {owed.length > 0 && (
              <ol className="promise-list" aria-label="Promises still owed">
                {owed.map(row)}
              </ol>
            )}
          </div>
          {kept.length > 0 && (
            <div className="stack tight">
              <h3>Kept ({kept.length})</h3>
              <ol className="promise-list" aria-label="Promises kept">
                {kept.map(row)}
              </ol>
            </div>
          )}
        </>
      )}
    </section>
  )
}
