import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type PassageRead, type SourceTextDetail, type TextMention } from '../api/client'
import { AllowanceNote } from '../components/Allowance'
import { MentionTextarea } from '../components/MentionTextarea'
import { NameQuestions } from '../components/NameQuestions'
import { Icon } from '../components/icons'
import {
  Button,
  ConfirmDialog,
  Dialog,
  ErrorNotice,
  Field,
  Loading,
  ReadOnlyNotice,
  WriteProblem,
} from '../components/ui'
import { segments, wordCount } from '../lib/texts'
import { plural, typeLabel } from '../lib/words'
import { entryPath, scopedKey, useCanonWrite, useEntries, useProject } from '../project'

type Part = { start: number; end: number | null }

/**
 * One text, as the author wrote it, with the names in it picked out. From here
 * the author answers who is who, and can have the assistant read it into
 * suggested changes. The words themselves are never altered.
 */
export function TextPage() {
  const { sourceId = '' } = useParams()
  const { scope, canWrite } = useProject()
  const navigate = useNavigate()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const [versionId, setVersionId] = useState<string | undefined>(undefined)
  const [replacing, setReplacing] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [result, setResult] = useState<PassageRead | null>(null)
  // The scene of the plan this text is the prose of, when the author says so (EXT-001).
  const [sceneId, setSceneId] = useState('')
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scenes = (outline.data?.items ?? []).filter((item) => item.kind === 'scene')

  const text = useQuery({
    queryKey: scopedKey(scope, 'text', sourceId, versionId ?? 'latest'),
    queryFn: () => api.text(scope, sourceId, versionId),
  })
  const detail = text.data

  // A change that points back here asks for its passage: "#at-120-180".
  const hash = useLocation().hash
  const cited = /^#at-(\d+)-(\d+)$/.exec(hash)
  const cite = cited ? { start: Number(cited[1]), end: Number(cited[2]) } : null
  useEffect(() => {
    if (detail && cited) document.getElementById('cited')?.scrollIntoView({ block: 'center' })
    // Only when the text arrives or the passage asked for changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.version.id, hash])

  const read = useCanonWrite<PassageRead, Part>(
    (expectedRevision, part) =>
      api.readPassage(scope, sourceId, {
        version_id: detail?.version.id ?? null,
        start: part.start,
        end: part.end,
        expected_revision: expectedRevision,
        // A scene's own text reads as that scene; a pasted text as the scene the author named.
        scene_id: (detail?.text.kind === 'scene' ? detail.text.scene_id : sceneId) || null,
      }),
    (found) => {
      setResult(found)
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'text') })
    },
  )
  const remove = useMutation({
    mutationFn: () => api.removeText(scope, sourceId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'texts') })
      navigate(`/projects/${scope.projectId}/texts`)
    },
  })

  if (text.isPending) return <Loading>Opening the text…</Loading>
  if (text.error || !detail) return <ErrorNotice error={text.error} prefix="Could not open this text" />

  const latest = detail.versions[detail.versions.length - 1]
  const isLatest = detail.version.id === latest.id
  const parts = detail.sections.length > 0 ? detail.sections : [{ index: 0, title: '', start: 0, end: detail.content.length }]
  const named = namedHere(detail)

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">
            <Link to={`/projects/${scope.projectId}/texts`}>Your writing</Link>
          </p>
          <h1>{detail.text.title}</h1>
          <p className="lede">
            {plural(wordCount(detail.content), 'word')}
            {detail.versions.length > 1 && ` · version ${detail.version.number} of ${detail.versions.length}`}
          </p>
        </div>
        {canWrite && (
          <div className="actions">
            <Button
              variant="primary"
              icon="spark"
              disabled={read.pending}
              onClick={() => void read.submit({ start: 0, end: null })}
            >
              {read.pending ? 'Reading…' : 'Have the assistant read it'}
            </Button>
            {detail.text.kind === 'scene' ? (
              // A scene's words may be shared with a what-if: they change only in its studio.
              detail.text.scene_id && (
                <Link className="btn" to={`/projects/${scope.projectId}/plan/${detail.text.scene_id}/write`}>
                  <Icon name="feather" size={15} />
                  Write it in the scene&rsquo;s studio
                </Link>
              )
            ) : (
              <details className="plan-more">
                <summary>Change or remove</summary>
                <div className="actions">
                  <Button size="sm" variant="ghost" onClick={() => setReplacing(true)}>
                    Change the words
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
                    Remove this text
                  </Button>
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {canWrite && detail.text.kind !== 'scene' && scenes.length > 0 && (
          <label className="stack tight read-as-scene">
            <span>Is this the writing of a scene in your plan? (optional)</span>
            <select value={sceneId} onChange={(event) => setSceneId(event.target.value)}>
              <option value="">No, read it on its own</option>
              {scenes.map((scene) => (
                <option key={scene.id} value={scene.id}>
                  {scene.title || 'A scene with no title'}
                </option>
              ))}
            </select>
            <span className="hint">
              Named, the assistant also suggests which of the plan’s threads it moves along or
              settles, as a change to that scene for you to review.
            </span>
          </label>
        )}
        {!isLatest && (
          <p className="notice plain" role="note">
            <span>This is an earlier version of the text.</span>
            <Button size="sm" onClick={() => setVersionId(undefined)}>
              Show the latest
            </Button>
          </p>
        )}
        <WriteProblem write={read} scope={scope} />
        {read.pending && <Loading>The assistant is reading. This can take a minute…</Loading>}
        {result && !read.pending && (
          <ReadResult
            result={result}
            projectId={scope.projectId}
            onNext={() => void read.submit({ start: result.end, end: null })}
            onClose={() => setResult(null)}
          />
        )}

        <div className="text-layout">
          <article className="text-body card" aria-label="The text">
            {parts.map((part) => (
              <section key={part.start} className="text-part">
                {canWrite && parts.length > 1 && (
                  <div className="text-part-tools">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="spark"
                      disabled={read.pending}
                      aria-label={`Have the assistant read ${part.title || 'the opening'}`}
                      onClick={() => void read.submit({ start: part.start, end: part.end })}
                    >
                      Read this part
                    </Button>
                  </div>
                )}
                <p className="text-words">
                  {segments(detail.content, detail.mentions, part.start, part.end, cite).map((segment, index, all) => {
                    const first = segment.cited && !all.slice(0, index).some((other) => other.cited)
                    const body = segment.mention ? (
                      <Named mention={segment.mention} names={detail.names} scopeLink={(entityId) => entryPath(scope, entityId)}>
                        {segment.text}
                      </Named>
                    ) : (
                      segment.text
                    )
                    return segment.cited ? (
                      <mark key={segment.start} id={first ? 'cited' : undefined}>
                        {body}
                      </mark>
                    ) : (
                      <span key={segment.start}>{body}</span>
                    )
                  })}
                </p>
              </section>
            ))}
          </article>

          <aside className="text-side stack">
            <NameQuestions detail={detail} entries={entries.items} />

            <section className="card stack tight" aria-labelledby="named-heading">
              <h2 id="named-heading">Named here</h2>
              {named.length === 0 ? (
                <p className="muted small">
                  Nothing from your story bible is named in this text yet.
                </p>
              ) : (
                <ul className="named-list">
                  {named.map(([entityId, count]) => {
                    const entry = entries.byId.get(entityId)
                    return (
                      <li key={entityId}>
                        <Link to={entryPath(scope, entityId)}>{detail.names[entityId] ?? 'An entry'}</Link>{' '}
                        <span className="muted small">
                          {entry ? `${typeLabel(entry.type)} · ` : ''}
                          {count === 1 ? 'once' : `${count} times`}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              )}
              <p className="muted small">
                To point at a name yourself, write <code>@Mara</code>, <code>@"the Fox"</code> or{' '}
                <code>[[the Old Mill]]</code> in the text.
              </p>
            </section>

            {canWrite && <AllowanceNote />}

            {detail.versions.length > 1 && (
              <section className="card stack tight" aria-labelledby="versions-heading">
                <h2 id="versions-heading">Versions</h2>
                <label className="sr-only" htmlFor="version-pick">
                  Version to show
                </label>
                <select
                  id="version-pick"
                  value={detail.version.id}
                  onChange={(event) => setVersionId(event.target.value === latest.id ? undefined : event.target.value)}
                >
                  {detail.versions.map((version) => (
                    <option key={version.id} value={version.id}>
                      Version {version.number}
                      {version.id === latest.id ? ' (latest)' : ''}
                    </option>
                  ))}
                </select>
                <p className="muted small">
                  Earlier versions are kept as they were, so anything taken from them still points
                  at the right words.
                </p>
              </section>
            )}
          </aside>
        </div>
      </div>

      {replacing && (
        <ReplaceWords
          detail={detail}
          onClose={() => setReplacing(false)}
          onSaved={() => {
            setReplacing(false)
            setVersionId(undefined)
            setResult(null)
          }}
        />
      )}
      {removing && (
        <ConfirmDialog
          title={`Remove “${detail.text.title}”?`}
          confirmLabel="Remove this text"
          keepLabel="Keep it"
          busy={remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate()}
          onCancel={() => setRemoving(false)}
        >
          Its words are erased and cannot be brought back from here. Your story bible is not
          changed: anything you already kept from this text stays, but can no longer show the
          words it came from.
        </ConfirmDialog>
      )}
    </>
  )
}

/** Entries the text names, most often named first. */
function namedHere(detail: SourceTextDetail): [string, number][] {
  const counts = new Map<string, number>()
  for (const mention of detail.mentions) {
    if (mention.status !== 'known') continue
    const entityId = mention.entity_ids[0]
    counts.set(entityId, (counts.get(entityId) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
}

function Named({
  mention,
  names,
  scopeLink,
  children,
}: {
  mention: TextMention
  names: Record<string, string>
  scopeLink: (entityId: string) => string
  children: string
}) {
  if (mention.status === 'known') {
    const entityId = mention.entity_ids[0]
    return (
      <Link className="mention known" to={scopeLink(entityId)} title={names[entityId] ?? undefined}>
        {children}
      </Link>
    )
  }
  const unclear = mention.status === 'unclear'
  return (
    <span
      className={`mention ${unclear ? 'unclear' : 'unknown'}`}
      title={unclear ? 'More than one entry answers to this name' : 'Not in your story bible yet'}
    >
      {children}
    </span>
  )
}

function ReadResult({
  result,
  projectId,
  onNext,
  onClose,
}: {
  result: PassageRead
  projectId: string
  onNext: () => void
  onClose: () => void
}) {
  const found = result.operation_count
  return (
    <section className={`notice ${found > 0 ? 'ok' : 'plain'} read-result`} role="status" aria-label="What the assistant found">
      <div className="stack tight grow">
        {found > 0 && result.mutation_id ? (
          <p>
            <strong>The assistant suggests {plural(found, 'change')} from what it read.</strong> Nothing
            is added until you have looked at them.
          </p>
        ) : (
          <p>
            <strong>Nothing new in what it read.</strong> Everything this part establishes is already in
            your story bible, or it could not be tied to the words.
          </p>
        )}
        {result.implied > 0 && (
          <p className="small">
            {result.implied === 1 ? 'One of them is' : `${result.implied} of them are`} only implied by
            the text, not stated. Each is marked.
          </p>
        )}
        {(result.thread_steps ?? 0) > 0 && (
          <p className="small">
            Among them: {plural(result.thread_steps ?? 0, 'step')} of the plan’s threads, as a change to the
            scene you named.
          </p>
        )}
        {result.guesses > 0 && (
          <p className="small">
            It also has {plural(result.guesses, 'question')} about who someone is, under “Who is this?”.
          </p>
        )}
        {result.discarded.length > 0 && (
          <details className="small">
            <summary>{plural(result.discarded.length, 'thing')} left out</summary>
            <ul>
              {result.discarded.map((item, index) => (
                <li key={index}>{item.reason}</li>
              ))}
            </ul>
          </details>
        )}
        {result.missing.length > 0 && (
          <details className="small">
            <summary>
              {plural(result.missing.length, 'thing')} your story bible has no way to record yet
            </summary>
            <ul>
              {result.missing.map((item, index) => (
                <li key={index}>
                  <strong>{item.name}</strong>
                  {item.reason ? `: ${item.reason}` : ''}
                </li>
              ))}
            </ul>
            <p>
              <Link to={`/projects/${projectId}/customize`}>Types and fields</Link> is where to add them.
            </p>
          </details>
        )}
        {result.more && (
          <p className="small">The text goes on past what was read in one go.</p>
        )}
      </div>
      <div className="actions">
        {found > 0 && result.mutation_id && (
          <Link className="btn primary sm" to={`/projects/${projectId}/review/${result.mutation_id}`}>
            Look at them
          </Link>
        )}
        {result.more && (
          <Button size="sm" icon="spark" onClick={onNext}>
            Read the next part
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onClose}>
          Dismiss
        </Button>
      </div>
    </section>
  )
}

function ReplaceWords({
  detail,
  onClose,
  onSaved,
}: {
  detail: SourceTextDetail
  onClose: () => void
  onSaved: () => void
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const [title, setTitle] = useState(detail.text.title)
  const [content, setContent] = useState(detail.content)
  const save = useMutation({
    mutationFn: () =>
      api.reviseText(scope, detail.text.id, {
        title: title.trim() !== detail.text.title ? title.trim() : null,
        content: content !== detail.content ? content : null,
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'text') })
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'texts') })
      onSaved()
    },
  })
  const ready = title.trim().length > 0 && content.trim().length > 0
  return (
    <Dialog title="Change the words" onClose={onClose} wide>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (ready) save.mutate()
        }}
      >
        <Field label="What to call it">
          {(props) => <input {...props} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} />}
        </Field>
        <Field
          label="The text"
          hint="Type @ to name someone or something from your story bible. Saving makes a new version. The earlier one is kept, so anything already taken from it still points at the right words."
        >
          {(props) => <MentionTextarea {...props} className="text-paste" value={content} onValueChange={setContent} rows={16} />}
        </Field>
        <ErrorNotice error={save.error} prefix="Could not save" />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save as a new version'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
