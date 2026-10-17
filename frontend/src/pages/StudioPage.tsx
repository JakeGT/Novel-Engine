import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  api,
  isApiError,
  type AssistantNote,
  type DraftCheck,
  type SceneDraft,
  type SceneReading,
  type SceneVersion,
  type Studio,
  type StudioJob,
} from '../api/client'
import { SceneContextDialog } from '../components/SceneContext'
import { DraftForm, JobOutcome, JobProgress } from '../components/StudioWork'
import { VoiceDialog } from '../components/VoiceDialog'
import {
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
} from '../components/ui'
import {
  DEFAULT_CHOICES,
  eitherOf,
  excerpt,
  forgetUnsaved,
  isFinished,
  isPlanJob,
  isReadJob,
  keepUnsaved,
  locate,
  missingFromCard,
  newRequestKey,
  NOTE_KINDS,
  orderChecks,
  orderNotes,
  parseAt,
  planCardProposal,
  readUnsaved,
  reviewPath,
  useSuggestedChanges,
  scrollAreaTo,
  type DraftChoices,
  type Span,
} from '../lib/studio'
import { wordCount } from '../lib/texts'
import { fullDate, timeAgo } from '../lib/time'
import { useWorkingWhatIf } from '../lib/whatif'
import { plural } from '../lib/words'
import { entryPath, scopedKey, useEntries, useProject } from '../project'

type View = 'text' | string

/**
 * The scene studio (ADR-022): the author writes a scene here, with checks beside the
 * words, drafts from the assistant waiting to be read, and every version kept. Nothing
 * the assistant writes replaces the author's words unless the author keeps it.
 */
export function StudioPage() {
  const { planId = '' } = useParams()
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const { whatIf } = useWorkingWhatIf(scope.projectId)
  const studioKey = scopedKey(scope, 'studio', planId)
  const studio = useQuery({ queryKey: studioKey, queryFn: () => api.studio(scope, planId) })
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scene = outline.data?.items.find((item) => item.id === planId)
  const data = studio.data

  // The words being written. Null means "the saved text, as it is". Words left unsaved
  // last time come back from this browser.
  const [editing, setEditing] = useState<string | null>(() => readUnsaved(scope, planId)?.text ?? null)
  const [cameBack, setCameBack] = useState(() => readUnsaved(scope, planId) !== null)
  const saved = data?.content ?? ''
  const value = editing ?? saved
  const dirty = editing !== null && editing !== saved

  const [view, setView] = useState<View>('text')
  const [marked, setMarked] = useState<Span | null>(null)
  const [asking, setAsking] = useState(false)
  const [choices, setChoices] = useState<DraftChoices>(DEFAULT_CHOICES)
  const requestKey = useRef<string>(newRequestKey())
  const [watching, setWatching] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<string[]>([])
  const [discarding, setDiscarding] = useState<{ draft: SceneDraft; label: string } | null>(null)
  const [reading, setReading] = useState<SceneVersion | null>(null)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [notice, setNotice] = useState<ReactNode>(null)
  const area = useRef<HTMLTextAreaElement>(null)

  // Opened from a change under review ("See it in the scene"): the passage it came from,
  // in the version it was read from, is found in the text as it is now and selected.
  const [search] = useSearchParams()
  const askedAt = parseAt(search.get('at'))
  const askedVersion = search.get('version')
  const needsOlder = Boolean(askedAt && askedVersion && data?.text_id && askedVersion !== data.version_id)
  const older = useQuery({
    queryKey: scopedKey(scope, 'text', data?.text_id, askedVersion),
    queryFn: () => api.text(scope, data?.text_id as string, askedVersion as string),
    enabled: needsOlder,
    staleTime: Infinity,
    retry: false,
  })
  const [citeClosed, setCiteClosed] = useState(false)
  const citeSelected = useRef(false)

  // Closing the tab with unsaved words asks first; the words are also kept in this browser.
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // A draft scrolls to the passage a note points at.
  useEffect(() => {
    if (marked && view !== 'text') document.getElementById('studio-mark')?.scrollIntoView?.({ block: 'center' })
  }, [marked, view])

  const write = (text: string) => {
    setEditing(text)
    if (text === saved) forgetUnsaved(scope, planId)
    else keepUnsaved(scope, planId, { text, base: data?.version_id ?? null })
  }

  const afterSave = (next: Studio) => {
    client.setQueryData(studioKey, next)
    forgetUnsaved(scope, planId)
    setEditing(null)
    setCameBack(false)
    void client.invalidateQueries({ queryKey: scopedKey(scope, 'texts') })
  }

  const save = useMutation({
    mutationFn: (content: string) => api.saveSceneText(scope, planId, content, data?.version_id ?? null),
    onSuccess: (next) => {
      afterSave(next)
      setNotice(null)
    },
    onError: (failure) => {
      // Someone else saved meanwhile: fetch their version so the author can save on top of it.
      if (isApiError(failure) && failure.status === 409) void client.invalidateQueries({ queryKey: studioKey })
    },
  })
  const conflict = isApiError(save.error) && save.error.status === 409
  const submitSave = () => {
    if (canWrite && dirty && !save.isPending) save.mutate(value)
  }

  // --- work the assistant does in the background ---------------------------------
  const running = data?.jobs.find((job) => !isFinished(job))
  // Work found still going when the page opens is followed to its end, like work started
  // here, so its outcome stays on screen after it finishes.
  if (running && !watching) setWatching(running.id)
  const jobId = watching ?? running?.id ?? null
  const job = useQuery({
    queryKey: scopedKey(scope, 'job', jobId),
    queryFn: async () => {
      const found = await api.job(scope, jobId as string)
      // When it finishes, what it made (drafts, notes) is read back with the scene.
      if (isFinished(found)) {
        await client.invalidateQueries({ queryKey: studioKey })
        // A reading or a plan card adds to the review queue.
        if (isReadJob(found) || isPlanJob(found)) void client.invalidateQueries({ queryKey: scopedKey(scope, 'mutations') })
      }
      return found
    },
    enabled: Boolean(jobId),
    refetchInterval: (query) => (isFinished(query.state.data) ? false : 1500),
  })
  const current: StudioJob | undefined = job.data ?? (running?.id === jobId ? running : undefined)
  const working = Boolean(current && !isFinished(current))

  const watch = (started: StudioJob) => {
    client.setQueryData(scopedKey(scope, 'job', started.id), started)
    setWatching(started.id)
    // New work makes the last thing that happened old news.
    setNotice(null)
  }
  const draft = useMutation({
    mutationFn: () =>
      api.startDraft(
        scope,
        planId,
        { variants: choices.variants, words: choices.words, direction: choices.direction.trim() },
        requestKey.current,
      ),
    onSuccess: (started) => {
      requestKey.current = newRequestKey()
      setAsking(false)
      watch(started)
    },
  })
  const review = useMutation({
    mutationFn: (draftId: string | null) => api.startReview(scope, planId, draftId),
    onSuccess: watch,
  })
  const readIn = useMutation({
    mutationFn: () => api.startReading(scope, planId),
    onSuccess: watch,
  })
  const planCard = useMutation({
    mutationFn: () => api.startPlanCard(scope, planId),
    onSuccess: watch,
  })
  const stop = useMutation({
    mutationFn: (id: string) => api.cancelJob(scope, id),
    onSuccess: (stopped) => client.setQueryData(scopedKey(scope, 'job', stopped.id), stopped),
  })

  // --- drafts waiting ------------------------------------------------------------
  const drafts = data?.drafts ?? []
  const labelOf = (draftId: string) => `Draft ${drafts.findIndex((item) => item.id === draftId) + 1}`
  const shown = view === 'text' ? null : (drafts.find((item) => item.id === view) ?? null)
  const keep = useMutation({
    mutationFn: (chosen: SceneDraft) => api.keepDraft(scope, chosen.id, data?.version_id ?? null),
    onSuccess: (next, chosen) => {
      const label = labelOf(chosen.id)
      const before = data?.versions[0]
      afterSave(next)
      setView('text')
      setMarked(null)
      // The draft's work is done with: its "ready" note gives way to what was kept.
      setDismissed((list) => [...list, chosen.job_id])
      setNotice(
        <>
          <strong>{label} is now your text</strong>, as version {next.versions[0]?.number}.
          {before ? ` Your earlier text is still there as version ${before.number}, in the list of versions.` : ''}
        </>,
      )
    },
    onError: (failure) => {
      if (isApiError(failure) && failure.status === 409) void client.invalidateQueries({ queryKey: studioKey })
    },
  })
  const discard = useMutation({
    mutationFn: (chosen: SceneDraft) => api.discardDraft(scope, chosen.id),
    onSuccess: async (_, chosen) => {
      if (view === chosen.id) setView('text')
      setDiscarding(null)
      await client.invalidateQueries({ queryKey: studioKey })
    },
  })

  // --- pointing at a passage -------------------------------------------------------
  const showInText = (span: Span) => {
    const box = area.current
    if (!box) return
    box.focus()
    box.setSelectionRange(span.start, span.end)
    scrollAreaTo(box, span.start)
    box.scrollIntoView?.({ block: 'nearest' })
  }

  // The words the passage was read from: undefined until they are here, null when that
  // version cannot be opened. The passage is then found in the words as they are now.
  const citeFrom =
    !askedAt || !data ? undefined : needsOlder ? (older.isPending ? undefined : (older.data?.content ?? null)) : data.content
  const cited =
    !askedAt || citeFrom === undefined ? undefined : citeFrom === null ? null : locate(citeFrom, value, askedAt.start, askedAt.end)
  const citeReady = cited !== undefined
  // Selected once, when it is first found.
  useEffect(() => {
    if (citeSelected.current || !citeReady) return
    citeSelected.current = true
    if (cited) showInText(cited)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citeReady])

  if (studio.isPending) return <Loading>Opening the scene…</Loading>
  if (!data) {
    return (
      <div className="stack">
        <ErrorNotice error={studio.error} prefix="Could not open this scene" />
        <p>
          <Link to={`/projects/${scope.projectId}/plan`}>Back to the story plan</Link>
        </p>
      </div>
    )
  }

  const pov = scene?.refs.find((ref) => ref.role === 'point_of_view')?.entity_id
  const place = scene?.refs.find((ref) => ref.role === 'place')?.entity_id
  const nameLink = (entityId: string) =>
    entries.byId.get(entityId) ? (
      <Link to={entryPath(scope, entityId)}>{entries.byId.get(entityId)?.name}</Link>
    ) : (
      'someone no longer in the story bible'
    )
  const outcomeJob = current && isFinished(current) && !dismissed.includes(current.id) ? current : null
  const nothingSaved = !data.version_id
  // A scene written before it was planned: the assistant can fill in its plan card.
  const missing = scene ? missingFromCard(scene) : []
  // Not offered again while its last suggestion is on screen, waiting to be reviewed.
  const planWaiting = Boolean(outcomeJob && isPlanJob(outcomeJob) && planCardProposal(outcomeJob))
  const offerPlanCard = canWrite && !nothingSaved && missing.length > 0 && !planWaiting
  const planBlocked = dirty ? 'Save first, so it reads what you have now.' : null

  // What the side column is about: the author's text, or the draft being read.
  const target = shown
    ? { checks: shown.checks, notes: shown.assistant, savedText: shown.content, liveText: shown.content }
    : { checks: data.checks, notes: data.assistant, savedText: saved, liveText: value }
  const point = (start?: number | null, end?: number | null) =>
    start === null || start === undefined || end === null || end === undefined
      ? undefined
      : (locate(target.savedText, target.liveText, start, end) ?? null)
  const onShow = (span: Span) => {
    if (shown) setMarked(span)
    else showInText(span)
  }
  const reviewBlocked = shown
    ? null
    : nothingSaved
      ? 'Save some words first, so there is something to read.'
      : dirty
        ? 'Save first, so it reads what you have now.'
        : null
  const readBlocked = nothingSaved
    ? 'Save some words first, so there is something to read.'
    : dirty
      ? 'Save first, so it reads what you have now.'
      : !data.reading.changed_since
        ? 'Nothing has changed since it was last read.'
        : null

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">
            <Link to={`/projects/${scope.projectId}/plan#plan-${planId}`}>Story plan</Link> · Scene
          </p>
          <h1>{data.title}</h1>
          {scene?.summary && <p className="lede">{scene.summary}</p>}
          {(pov || place) && (
            <p className="plan-about studio-plan">
              {pov && <span>Seen by {nameLink(pov)}</span>}
              {place && <span>At {nameLink(place)}</span>}
            </p>
          )}
        </div>
        <div className="actions">
          <Button onClick={() => setVoiceOpen(true)}>
            How you write
          </Button>
          {canWrite && (
            <Button
              variant="primary"
              icon="spark"
              aria-expanded={asking}
              aria-controls="studio-ask"
              disabled={working}
              onClick={() => setAsking(true)}
            >
              Draft with the assistant
            </Button>
          )}
        </div>
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {offerPlanCard && (
          <div className="notice plain studio-plan-fill">
            <p className="small">
              <strong>No {eitherOf(missing)} planned for this scene yet.</strong> The assistant can
              suggest {missing.length === 1 ? 'it' : 'them'}, and who is in the scene, from what you
              have written. Nothing changes in your plan until you review it.
            </p>
            <div className="actions">
              <Button
                size="sm"
                icon="spark"
                disabled={working || planCard.isPending || planBlocked !== null}
                aria-describedby={planBlocked ? 'plan-blocked' : undefined}
                onClick={() => planCard.mutate()}
              >
                {planCard.isPending ? 'Starting…' : 'Fill in the plan card from the prose'}
              </Button>
              {planBlocked && (
                <span id="plan-blocked" className="hint">
                  {planBlocked}
                </span>
              )}
            </div>
            <AssistantStartProblem error={planCard.error} prefix="Could not start filling in the plan card" />
          </div>
        )}
        {notice && (
          <div className="notice ok" role="status">
            <p>{notice}</p>
            <Button size="sm" variant="ghost" onClick={() => setNotice(null)}>
              Dismiss
            </Button>
          </div>
        )}
        {citeReady && !citeClosed && (
          <div className="notice plain small" role="status">
            <p>
              {cited
                ? 'The words the change came from are selected in your text.'
                : 'The words the change came from are no longer in this scene: it has changed since they were read. This is the scene as it is now.'}
            </p>
            <Button size="sm" variant="ghost" onClick={() => setCiteClosed(true)}>
              Dismiss
            </Button>
          </div>
        )}
        {cameBack && dirty && (
          <p className="notice plain" role="note">
            <span>
              <strong>Your unsaved words from last time are back.</strong> Save them when you are
              ready, or put back the saved text.
            </span>
            <Button
              size="sm"
              onClick={() => {
                write(saved)
                setCameBack(false)
              }}
            >
              Put back the saved text
            </Button>
          </p>
        )}

        {working && current && (
          <JobProgress
            job={current}
            stopping={stop.isPending}
            error={stop.error}
            onStop={() => stop.mutate(current.id)}
          />
        )}
        {outcomeJob && (
          <JobOutcome
            job={outcomeJob}
            drafts={drafts.filter((item) => item.job_id === outcomeJob.id)}
            onRead={(draftId) => {
              setView(draftId)
              setMarked(null)
            }}
            onRetry={() => {
              setDismissed((list) => [...list, outcomeJob.id])
              if (outcomeJob.kind === 'scene_draft') setAsking(true)
              else if (isReadJob(outcomeJob)) readIn.mutate()
              else if (isPlanJob(outcomeJob)) planCard.mutate()
              else review.mutate(shown?.id ?? null)
            }}
            onDismiss={() => setDismissed((list) => [...list, outcomeJob.id])}
          />
        )}
        <ErrorNotice error={job.error} prefix="Could not see how the work is going" />

        <div id="studio-ask" hidden={!asking || !canWrite}>
          {asking && canWrite && (
            <DraftForm
              planId={planId}
              choices={choices}
              onChange={setChoices}
              onStart={() => draft.mutate()}
              onClose={() => setAsking(false)}
              onShowContext={scene ? () => setContextOpen(true) : undefined}
              starting={draft.isPending}
              error={draft.error}
              busy={working}
            />
          )}
        </div>

        <div className="studio-layout">
          <div className="studio-main stack">
            {drafts.length > 0 && (
              <div className="studio-views">
                <div className="segmented" role="group" aria-label="What you are reading">
                  <button type="button" aria-pressed={view === 'text'} onClick={() => setView('text')}>
                    Your text
                  </button>
                  {drafts.map((item, index) => (
                    <button
                      key={item.id}
                      type="button"
                      aria-pressed={view === item.id}
                      onClick={() => {
                        setView(item.id)
                        setMarked(null)
                      }}
                    >
                      Draft {index + 1}
                    </button>
                  ))}
                </div>
                <p className="hint">
                  {drafts.length === 1
                    ? 'A draft from the assistant is waiting.'
                    : `${drafts.length} drafts from the assistant are waiting.`}{' '}
                  Nothing changes your text until you keep one.
                </p>
              </div>
            )}

            {shown ? (
              <DraftView
                draft={shown}
                label={labelOf(shown.id)}
                marked={marked}
                canWrite={canWrite}
                dirty={dirty}
                keeping={keep.isPending}
                error={keep.error}
                onKeep={() => keep.mutate(shown)}
                onDiscard={() => setDiscarding({ draft: shown, label: labelOf(shown.id) })}
              />
            ) : (
              <section className="studio-writing" aria-labelledby="writing-heading">
                <h2 id="writing-heading" className="sr-only">
                  Your text
                </h2>
                <textarea
                  ref={area}
                  className="studio-surface"
                  aria-label={`The text of “${data.title}”`}
                  aria-describedby="studio-status"
                  value={value}
                  readOnly={!canWrite}
                  spellCheck
                  placeholder={
                    canWrite
                      ? 'Write the scene here, or ask the assistant for a draft to start from.'
                      : 'Nothing has been written for this scene yet.'
                  }
                  onChange={(event) => {
                    write(event.target.value)
                    save.reset()
                    setCiteClosed(true)
                  }}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                      event.preventDefault()
                      submitSave()
                    }
                  }}
                />
                <div className="studio-bar">
                  <p id="studio-status" className="save-state" aria-live="polite">
                    <span className={`dot ${dirty ? 'warn' : ''}`} aria-hidden="true" />
                    {plural(wordCount(value), 'word')} ·{' '}
                    {save.isPending
                      ? 'Saving…'
                      : dirty
                        ? 'Not saved yet'
                        : nothingSaved
                          ? 'Nothing saved yet'
                          : `Saved${data.versions[0] ? ` as version ${data.versions[0].number}` : ''}`}
                    {scope.branchId && whatIf ? ` · in the what-if “${whatIf.name}” only` : ''}
                  </p>
                  {canWrite && (
                    <span className="actions">
                      <span className="hint">Ctrl S saves</span>
                      <Button variant="primary" disabled={!dirty || save.isPending} onClick={submitSave}>
                        {save.isPending ? 'Saving…' : 'Save'}
                      </Button>
                    </span>
                  )}
                </div>
                {conflict ? (
                  <div className="notice warn" role="alert">
                    <p>
                      <strong>Someone else saved this scene while you were writing.</strong> Nothing
                      was saved, and your words are still here. Saving now makes yours the newest
                      version; theirs stays in the list of versions.
                    </p>
                    <Button onClick={submitSave} disabled={save.isPending || studio.isFetching}>
                      Save mine as the newest version
                    </Button>
                  </div>
                ) : (
                  <ErrorNotice error={save.error} prefix="Not saved" />
                )}
              </section>
            )}

            <Versions
              versions={data.versions}
              onRead={setReading}
            />
          </div>

          <aside className="studio-side stack" aria-label={shown ? `Notes on ${labelOf(shown.id)}` : 'Notes on your text'}>
            <Checks
              checks={target.checks}
              text={target.liveText}
              point={point}
              onShow={onShow}
              stale={!shown && dirty}
              empty={!shown && nothingSaved}
            />
            <Notes
              notes={target.notes}
              text={target.liveText}
              point={point}
              onShow={onShow}
              stale={!shown && dirty}
              action={
                canWrite ? (
                  <div className="stack tight">
                    <Button
                      size="sm"
                      icon="spark"
                      disabled={working || review.isPending || reviewBlocked !== null}
                      aria-describedby={reviewBlocked ? 'review-blocked' : undefined}
                      onClick={() => review.mutate(shown?.id ?? null)}
                    >
                      {review.isPending
                        ? 'Starting…'
                        : target.notes.length > 0
                          ? 'Check again with the assistant'
                          : 'Check with the assistant'}
                    </Button>
                    {reviewBlocked && (
                      <span id="review-blocked" className="hint">
                        {reviewBlocked}
                      </span>
                    )}
                    <AssistantStartProblem error={review.error} prefix="Could not start the check" />
                  </div>
                ) : null
              }
            />
            <StoryReading
              reading={data.reading}
              action={
                canWrite ? (
                  <div className="stack tight">
                    <Button
                      size="sm"
                      icon="spark"
                      disabled={working || readIn.isPending || readBlocked !== null}
                      aria-describedby={readBlocked ? 'read-blocked' : undefined}
                      onClick={() => readIn.mutate()}
                    >
                      {readIn.isPending ? 'Starting…' : 'Update the story from this scene'}
                    </Button>
                    {readBlocked && (
                      <span id="read-blocked" className="hint">
                        {readBlocked}
                      </span>
                    )}
                    <AssistantStartProblem error={readIn.error} prefix="Could not start reading the scene" />
                  </div>
                ) : null
              }
            />
          </aside>
        </div>
      </div>

      {voiceOpen && <VoiceDialog onClose={() => setVoiceOpen(false)} />}
      {contextOpen && scene && <SceneContextDialog scene={scene} onClose={() => setContextOpen(false)} />}
      {reading && data.text_id && (
        <VersionDialog
          textId={data.text_id}
          version={reading}
          isCurrent={reading.id === data.version_id}
          canRestore={canWrite && !dirty}
          onRestore={(content) => {
            write(content)
            setView('text')
            setReading(null)
            setNotice(
              <>
                <strong>Version {reading.number} is in the editor.</strong> Save to make it the newest
                version; nothing else has changed.
              </>,
            )
          }}
          onClose={() => setReading(null)}
        />
      )}
      {discarding && (
        <ConfirmDialog
          title={`Discard ${discarding.label}?`}
          confirmLabel={`Discard ${discarding.label}`}
          keepLabel="Keep it waiting"
          busy={discard.isPending}
          error={discard.error}
          onConfirm={() => discard.mutate(discarding.draft)}
          onCancel={() => setDiscarding(null)}
        >
          The draft is deleted and cannot be brought back. Your own text and its versions are not
          affected.
        </ConfirmDialog>
      )}
    </>
  )
}

function AssistantStartProblem({ error, prefix }: { error: unknown; prefix: string }) {
  if (!error) return null
  return <ErrorNotice error={error} prefix={prefix} />
}

/**
 * Reading the scene into the story bible (Phase 6): when it was last read, whether it has
 * changed since, and what came of it. What it finds waits in the review queue.
 */
function StoryReading({
  reading,
  action,
}: {
  reading: SceneReading
  action: ReactNode
}) {
  const { scope } = useProject()
  const count = useSuggestedChanges(reading.proposals)
  const read = Boolean(reading.read_at)
  const when = reading.read_at ? (
    <time dateTime={reading.read_at} title={fullDate(reading.read_at)}>
      {timeAgo(reading.read_at)}
    </time>
  ) : null
  const proposals = reading.proposals.length
  return (
    <section className="stack tight" aria-labelledby="reading-heading">
      <div>
        <h2 id="reading-heading" className="studio-side-title">
          Your story bible
        </h2>
        <p className="hint">
          The assistant reads your saved text, finds the people, places and facts it states, quotes
          the words for each, and suggests them for you to review. After the first time, it reads
          only the paragraphs that changed.
        </p>
      </div>
      {!read ? (
        <p className="small">Not read into the story yet.</p>
      ) : (
        <div className="stack tight small">
          <p>
            {reading.changed_since ? (
              <>
                <strong>The scene has changed since it was last read</strong>
                {reading.version_number ? <> at version {reading.version_number}</> : null}, {when}.
              </>
            ) : (
              <>
                Read into the story{reading.version_number ? <> at version {reading.version_number}</> : null}, {when}.
              </>
            )}
          </p>
          <p>
            {proposals === 0 ? (
              'Nothing new came of it.'
            ) : (
              <>
                {count === null
                  ? 'It suggested changes. '
                  : `It suggested ${plural(count, 'change')}. `}
                <Link to={reviewPath(scope.projectId, reading.proposals)}>
                  {proposals === 1 ? 'See what it suggested' : 'See them in the review queue'}
                </Link>
              </>
            )}
          </p>
        </div>
      )}
      {action}
    </section>
  )
}

type Point = (start?: number | null, end?: number | null) => Span | null | undefined

/** Where a note points: a quotation and a way to go to it, or why it can no longer be found. */
function Passage({ text, span, onShow }: { text: string; span: Span | null | undefined; onShow: (span: Span) => void }) {
  if (span === undefined) return null
  if (span === null) return <p className="hint">The passage it was about has changed since.</p>
  return (
    <p className="finding-passage">
      <q>{excerpt(text, span)}</q>{' '}
      <button type="button" className="link-button" onClick={() => onShow(span)}>
        Show in the text
      </button>
    </p>
  )
}

function Checks({
  checks,
  text,
  point,
  onShow,
  stale,
  empty,
}: {
  checks: DraftCheck[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  empty: boolean
}) {
  const ordered = orderChecks(checks)
  return (
    <section className="stack tight" aria-labelledby="checks-heading">
      <div>
        <h2 id="checks-heading" className="studio-side-title">
          Checks
        </h2>
        <p className="hint">Against your story bible and plan, every time the text changes. No assistant needed.</p>
      </div>
      {stale && <p className="hint">These are about the saved text. Save to check again.</p>}
      {empty ? (
        <p className="muted small">Nothing to check until there are some words.</p>
      ) : ordered.length === 0 ? (
        <p className="muted small">Nothing to point out.</p>
      ) : (
        <ul className="findings">
          {ordered.map((check, index) => (
            <li key={`${check.code}-${index}`} className={`finding ${check.severity}`}>
              <Badge tone={check.severity === 'warning' ? 'warn' : 'neutral'}>
                {check.severity === 'warning' ? 'Worth checking' : 'Note'}
              </Badge>
              <p>{check.message}</p>
              <Passage text={text} span={point(check.start, check.end)} onShow={onShow} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Notes({
  notes,
  text,
  point,
  onShow,
  stale,
  action,
}: {
  notes: AssistantNote[]
  text: string
  point: Point
  onShow: (span: Span) => void
  stale: boolean
  action: ReactNode
}) {
  return (
    <section className="stack tight" aria-labelledby="notes-heading">
      <div>
        <h2 id="notes-heading" className="studio-side-title">
          The assistant’s notes
        </h2>
        <p className="hint">
          Only when you ask. It reads for continuity, viewpoint, the plan, your voice and clarity,
          and every note quotes your words.
        </p>
      </div>
      {stale && notes.length > 0 && <p className="hint">These are about the saved text.</p>}
      {notes.length > 0 && (
        <ul className="findings">
          {orderNotes(notes).map((note) => (
            <li key={note.id} className="finding assistant">
              <Badge tone="info">{NOTE_KINDS[note.category] ?? 'Note'}</Badge>
              <p>{note.message}</p>
              {note.suggestion && <p className="small finding-suggestion">Suggestion: {note.suggestion}</p>}
              <Passage text={text} span={point(note.start, note.end)} onShow={onShow} />
            </li>
          ))}
        </ul>
      )}
      {action}
    </section>
  )
}

/** A draft as it will read, with its one clear choice: keep it, or discard it. */
function DraftView({
  draft,
  label,
  marked,
  canWrite,
  dirty,
  keeping,
  error,
  onKeep,
  onDiscard,
}: {
  draft: SceneDraft
  label: string
  marked: Span | null
  canWrite: boolean
  dirty: boolean
  keeping: boolean
  error: unknown
  onKeep: () => void
  onDiscard: () => void
}) {
  const span = marked && marked.end <= draft.content.length ? marked : null
  return (
    <section className="studio-draft stack" aria-labelledby="draft-heading">
      <div className="studio-draft-head">
        <div>
          <h2 id="draft-heading">{label}</h2>
          <p className="hint">
            {plural(draft.words, 'word')} · written by the assistant {timeAgo(draft.created_at)}
          </p>
        </div>
      </div>
      <div className="studio-page text-words" tabIndex={0} aria-label={`The text of ${label}`}>
        {span ? (
          <>
            {draft.content.slice(0, span.start)}
            <mark id="studio-mark">{draft.content.slice(span.start, span.end)}</mark>
            {draft.content.slice(span.end)}
          </>
        ) : (
          draft.content
        )}
      </div>
      {canWrite && (
        <div className="studio-draft-actions">
          <div className="stack tight">
            <span className="actions">
              <Button variant="primary" icon="check" disabled={dirty || keeping} onClick={onKeep}>
                {keeping ? 'Keeping…' : `Keep ${label}`}
              </Button>
            </span>
            <span className="hint">
              {dirty
                ? 'Save your own changes first, so they are kept as a version too.'
                : 'It becomes the newest version of the scene. Your current text stays in the list of versions.'}
            </span>
          </div>
          <Button variant="danger-quiet" onClick={onDiscard} disabled={keeping}>
            Discard {label}
          </Button>
        </div>
      )}
      {isApiError(error) && error.status === 409 ? (
        <p className="notice warn" role="alert">
          <span>
            <strong>The scene changed since this page was opened.</strong> Nothing was kept. The
            newest text has been fetched; keep the draft again to put it on top.
          </span>
        </p>
      ) : (
        <ErrorNotice error={error} prefix="Not kept" />
      )}
    </section>
  )
}

const WHO: Record<string, string> = { author: 'You', assistant: 'The assistant' }

function Versions({ versions, onRead }: { versions: SceneVersion[]; onRead: (version: SceneVersion) => void }) {
  return (
    <section className="stack tight" aria-labelledby="versions-heading">
      <h2 id="versions-heading">Versions</h2>
      {versions.length === 0 ? (
        <p className="muted small">Every time you save or keep a draft, a version is added here. None yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="diff-table studio-versions">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Words</th>
                <th scope="col">Written by</th>
                <th scope="col">When</th>
                <th scope="col">
                  <span className="sr-only">Read</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version, index) => (
                <tr key={version.id}>
                  <td>
                    {version.number}
                    {index === 0 && <span className="muted small"> · current</span>}
                  </td>
                  <td>{version.words.toLocaleString()}</td>
                  <td>{WHO[version.origin] ?? 'You'}</td>
                  <td>
                    <time dateTime={version.created_at} title={fullDate(version.created_at)}>
                      {timeAgo(version.created_at)}
                    </time>
                  </td>
                  <td>
                    <Button size="sm" variant="ghost" onClick={() => onRead(version)}>
                      Read version {version.number}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function VersionDialog({
  textId,
  version,
  isCurrent,
  canRestore,
  onRestore,
  onClose,
}: {
  textId: string
  version: SceneVersion
  isCurrent: boolean
  canRestore: boolean
  onRestore: (content: string) => void
  onClose: () => void
}) {
  const { scope, canWrite } = useProject()
  const text = useQuery({
    queryKey: scopedKey(scope, 'text', textId, version.id),
    queryFn: () => api.text(scope, textId, version.id),
  })
  return (
    <Dialog title={`Version ${version.number}`} onClose={onClose} wide>
      <div className="stack">
        <p className="hint">
          {plural(version.words, 'word')} · {WHO[version.origin] ?? 'You'} · {fullDate(version.created_at)}
        </p>
        {text.isPending && <Loading>Opening this version…</Loading>}
        <ErrorNotice error={text.error} prefix="Could not open this version" />
        {text.data && (
          <div className="text-words studio-page scroll-list" tabIndex={0} aria-label={`The text of version ${version.number}`}>
            {text.data.content}
          </div>
        )}
        <div className="actions end">
          <Button onClick={onClose}>Close</Button>
          {canWrite && !isCurrent && text.data && (
            <Button variant="primary" disabled={!canRestore} onClick={() => onRestore(text.data.content)}>
              Put these words in the editor
            </Button>
          )}
        </div>
        {canWrite && !isCurrent && !canRestore && (
          <p className="hint">Save or put back your unsaved words first, so nothing you typed is lost.</p>
        )}
      </div>
    </Dialog>
  )
}
