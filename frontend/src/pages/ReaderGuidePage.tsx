import { useQuery } from '@tanstack/react-query'
import { useId } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type GuidePart, type ReaderGuide } from '../api/client'
import { Button, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { guideFileName } from '../lib/guide'
import { fullDate } from '../lib/time'
import { scopedKey, useEntries, useProject } from '../project'

type Guide = ReaderGuide

/** The parts a guide can be made of on its own (QRY-002), in the author's words. */
const PARTS: { value: GuidePart; label: string; hint: string }[] = [
  { value: 'everything', label: 'Everything', hint: 'People, places and everything else a reader could know.' },
  { value: 'characters', label: 'Characters', hint: 'Only the people, with what a reader could know about each.' },
  { value: 'places', label: 'Places', hint: 'Only the places, with what a reader could know about each.' },
  {
    value: 'timeline',
    label: 'Timeline of the scenes so far',
    hint: 'The scenes read so far and what happened in them, in story-time order. Events the story has not told yet stay out.',
  },
  {
    value: 'recap',
    label: 'Recap, scene by scene',
    hint: 'Each scene so far: whose eyes, where, who is there, and what it shows the reader. Hints and your own plan notes stay out.',
  },
]
const PART_EYEBROW: Record<GuidePart, string> = {
  everything: 'Reader’s guide',
  characters: 'Reader’s guide · characters',
  places: 'Reader’s guide · places',
  timeline: 'Reader’s guide · timeline',
  recap: 'Reader’s guide · recap',
}
const NOTHING_YET: Record<GuidePart, { title: string; body: string }> = {
  everything: {
    title: 'Nobody has appeared yet',
    body: 'No one and nowhere has come into the story by this scene, so there is nothing a reader could know.',
  },
  characters: { title: 'No one has appeared yet', body: 'No character has come into the story by this scene.' },
  places: { title: 'No place has appeared yet', body: 'No place has come into the story by this scene.' },
  timeline: {
    title: 'Nothing placed yet',
    body: 'No scene read so far is placed in story time, and nothing happened in one. Give a scene a time in the plan to place it.',
  },
  recap: { title: 'No scenes yet', body: 'There are no scenes up to this one to recap.' },
}

function isPart(value: string): value is GuidePart {
  return PARTS.some((part) => part.value === value)
}

const REVOKE_AFTER = 60_000
// How many versions the picker lists; it says so when there are more.
const VERSIONS_LISTED = 100

function download(guide: Guide) {
  const url = URL.createObjectURL(new Blob([guide.markdown], { type: 'text/markdown' }))
  const link = document.createElement('a')
  link.href = url
  link.download = guideFileName(guide.title)
  link.click()
  // Revoked a moment later: revoking at once can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER)
}

/**
 * What a reader could know after one scene, as a page to read, print or download (QRY-002).
 * The guide is bound to the line of the story, the version, the scene and whose knowledge
 * it keeps; the same binding heads the printed page and the Markdown file.
 */
export function ReaderGuidePage() {
  const { planId = '' } = useParams()
  const { scope, headRevision } = useProject()
  const [params, setParams] = useSearchParams()
  const viewpoint = params.get('as') ?? ''
  const versionText = params.get('version') ?? ''
  const version = /^\d+$/.test(versionText) ? Number(versionText) : undefined
  const partText = params.get('part') ?? ''
  const part: GuidePart = isPart(partText) ? partText : 'everything'
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const whoId = useId()
  const versionId = useId()
  const partId = useId()

  const guide = useQuery({
    // The latest guide changes with every saved change; an earlier version never does.
    queryKey: scopedKey(scope, 'reader-guide', planId, viewpoint, version ?? `latest-${headRevision}`, part),
    queryFn: () =>
      api.readerGuide(scope, planId, viewpoint || undefined, version, part === 'everything' ? undefined : part),
    enabled: Boolean(planId),
  })
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions'),
    queryFn: () => api.listRevisions(scope, { limit: VERSIONS_LISTED }),
  })
  const names = useQuery({ queryKey: scopedKey(scope, 'version-names'), queryFn: () => api.versionNames(scope) })
  const namesFor = (revision: number) =>
    (names.data?.items ?? []).filter((item) => item.revision === revision).map((item) => item.name)
  const earlier = (revisions.data?.items ?? []).filter((item) => item.revision < headRevision)
  const listed = new Set(earlier.map((item) => item.revision))

  const choose = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const data = guide.data
  return (
    <>
      <div className="page-head no-print">
        <div>
          <p className="eyebrow">
            <Link to={`/projects/${scope.projectId}/plan#plan-${planId}`}>Story plan</Link>
          </p>
          <h1>Reader’s guide</h1>
          <p className="lede">
            Everything a reader could know after this scene, or one part of it: the characters, the places, a
            timeline or a recap. Print it, save it as a PDF from the print window, or download it as a Markdown
            file any editor can open.
          </p>
        </div>
      </div>

      <section className="card guide-controls no-print" aria-label="Which guide">
        <div className="guide-choices">
          <div className="field">
            <label htmlFor={partId}>What to include</label>
            <select
              id={partId}
              value={part}
              aria-describedby={`${partId}-hint`}
              onChange={(event) => choose('part', event.target.value === 'everything' ? '' : event.target.value)}
            >
              {PARTS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <span id={`${partId}-hint`} className="hint">
              {PARTS.find((item) => item.value === part)?.hint}
            </span>
          </div>
          <div className="field">
            <label htmlFor={whoId}>Whose guide</label>
            <select id={whoId} value={viewpoint} onChange={(event) => choose('as', event.target.value)}>
              <option value="">The reader’s</option>
              {characters.map((item) => (
                <option key={item.id} value={item.id}>
                  As {item.name} knows it
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={versionId}>Version of the story</label>
            <select id={versionId} value={version ? String(version) : ''} onChange={(event) => choose('version', event.target.value)}>
              <option value="">The latest (version {headRevision})</option>
              {version && version < headRevision && !listed.has(version) && (
                <option value={String(version)}>Version {version}</option>
              )}
              {earlier.map((item) => {
                const named = namesFor(item.revision)
                return (
                  <option key={item.revision} value={String(item.revision)}>
                    Version {item.revision}
                    {named.length > 0 ? ` · ${named.join(', ')}` : ''} · {fullDate(item.created_at)}
                  </option>
                )
              })}
            </select>
            <span className="hint">
              An earlier version shows the story as it stood then: its names, places and plan.
              {(revisions.data?.items.length ?? 0) >= VERSIONS_LISTED &&
                ` Only the latest ${VERSIONS_LISTED} versions are listed here.`}
            </span>
          </div>
        </div>
        <div className="actions">
          <Button variant="primary" disabled={!data} onClick={() => window.print()}>
            Print or save as PDF
          </Button>
          <Button disabled={!data} onClick={() => data && download(data)}>
            Download as Markdown
          </Button>
        </div>
        <p className="hint">
          There is no link to share this guide with people outside the project yet: such a link needs its own
          rules for who may open it. Print it or download it to share it.
        </p>
      </section>

      {guide.isPending && <Loading>Making the guide…</Loading>}
      <ErrorNotice error={guide.error} prefix="Could not make the guide" />
      {data && <GuideSheet guide={data} />}
    </>
  )
}

/** The guide itself, laid out like a page of a story bible. This is what prints. */
export function GuideSheet({ guide }: { guide: Guide }) {
  const titleId = useId()
  const part: GuidePart = guide.part ?? 'everything'
  const nothing = NOTHING_YET[part]
  // The page's own title is the h1; the guide's title sits under it, and is what prints.
  return (
    <article className="guide-sheet" aria-labelledby={titleId}>
      <header className="guide-head">
        <p className="eyebrow">{PART_EYEBROW[part]}</p>
        <h2 id={titleId} className="guide-title">
          {guide.title}
        </h2>
        <p className="guide-binding">{guide.bound_to}</p>
        <p className="guide-leaves-out">{guide.leaves_out}</p>
      </header>
      {guide.sections.length === 0 ? (
        <EmptyState icon="book" title={nothing.title}>
          {nothing.body}
        </EmptyState>
      ) : (
        guide.sections.map((section, index) => (
          <section key={`${index}:${section.heading}`} className="guide-section" aria-label={section.heading}>
            <h3>{section.heading}</h3>
            {section.entries.map((entry, index) => (
              <div key={`${index}:${entry.name}`} className="guide-entry">
                <h4>{entry.name}</h4>
                {entry.lines.length > 0 ? (
                  <ul>
                    {entry.lines.map((line, number) => (
                      <li key={`${number}:${line}`}>{line}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="guide-none">Nothing more is known yet.</p>
                )}
              </div>
            ))}
          </section>
        ))
      )}
    </article>
  )
}
