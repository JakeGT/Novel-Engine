import { useQuery } from '@tanstack/react-query'
import { useId } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type ReaderGuide } from '../api/client'
import { Button, EmptyState, ErrorNotice, Loading } from '../components/ui'
import { guideFileName } from '../lib/guide'
import { fullDate } from '../lib/time'
import { scopedKey, useEntries, useProject } from '../project'

type Guide = ReaderGuide

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
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const whoId = useId()
  const versionId = useId()

  const guide = useQuery({
    // The latest guide changes with every saved change; an earlier version never does.
    queryKey: scopedKey(scope, 'reader-guide', planId, viewpoint, version ?? `latest-${headRevision}`),
    queryFn: () => api.readerGuide(scope, planId, viewpoint || undefined, version),
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
            Everything a reader could know after this scene. Print it, save it as a PDF from the print window,
            or download it as a Markdown file any editor can open.
          </p>
        </div>
      </div>

      <section className="card guide-controls no-print" aria-label="Which guide">
        <div className="guide-choices">
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
  // The page's own title is the h1; the guide's title sits under it, and is what prints.
  return (
    <article className="guide-sheet" aria-labelledby={titleId}>
      <header className="guide-head">
        <p className="eyebrow">Reader’s guide</p>
        <h2 id={titleId} className="guide-title">
          {guide.title}
        </h2>
        <p className="guide-binding">{guide.bound_to}</p>
        <p className="guide-leaves-out">{guide.leaves_out}</p>
      </header>
      {guide.sections.length === 0 ? (
        <EmptyState icon="book" title="Nobody has appeared yet">
          No one and nowhere has come into the story by this scene, so there is nothing a reader could know.
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
