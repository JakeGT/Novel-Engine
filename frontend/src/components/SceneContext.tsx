import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type ContextComparison,
  type ContextItem,
  type ContextSettings,
  type KeptContext,
  type PlanItem,
  type SceneContext as SceneContextData,
} from '../api/client'
import { fullDate } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useInvalidateProject, useProject } from '../project'
import { SceneSummaryPanel } from './SceneSummary'
import { Button, Dialog, ErrorNotice, Loading, WriteProblem } from './ui'

const WAITING_EVERY = 2000

// How much to give, in words, as named choices rather than a number to invent.
const BUDGETS: { value: number; label: string }[] = [
  { value: 1000, label: 'Short — about 1,000 words' },
  { value: 2500, label: 'Usual — about 2,500 words' },
  { value: 5000, label: 'Long — about 5,000 words' },
  { value: 10000, label: 'Very long — about 10,000 words' },
]

const LEFT_OUT: Record<string, string> = {
  excluded: 'You said not to use these',
  budget: 'No room within the length you chose',
  secret: 'Secrets the viewpoint character does not know',
}

// Why something given for a kept draft is not given now.
const NOW_LEFT_OUT: Record<string, string> = {
  excluded: 'You said not to use it',
  budget: 'No room now within the length you chose',
  secret: 'A secret the viewpoint character does not know, now left out',
}

const WHAT_CHANGED: Record<string, string> = {
  words: 'its words',
  section: 'where it sits',
  reason: 'why it is given',
}

// What moved an item up or down (CTX-007), in a few words; the reason says it in full.
function signalLabel(name: string, effect: number): string {
  const up = effect > 0
  switch (name) {
    case 'importance':
      return up ? 'Important to you' : 'Marked as less central'
    case 'continuity':
      return 'Has an open warning'
    case 'distance':
      return 'Further from this scene'
    case 'diversity':
      return 'Like another passage'
    case 'meaning':
      return 'Close in meaning'
    case 'thread':
      return 'On the same thread'
    case 'out of date':
      return 'Out of date'
    default:
      return up ? 'Ranked higher' : 'Ranked lower'
  }
}

/** How each signal moved an item, as small labelled marks; nothing for a fixed score. */
function RankMarks({ item }: { item: ContextItem }) {
  const signals = item.signals ?? []
  if (signals.length === 0) return null
  return (
    <span className="rank-marks" aria-label="What moved it up or down">
      {signals.map((signal, index) => (
        <span
          key={`${signal.name}-${index}`}
          className={`mark rank-mark ${signal.effect > 0 ? 'up' : 'down'}`}
        >
          {signal.effect > 0 ? 'Raised: ' : 'Lowered: '}
          {signalLabel(signal.name, signal.effect)}
        </span>
      ))}
    </span>
  )
}

function waitingWords(index: NonNullable<SceneContextData['index']>): string | null {
  const waiting = index.waiting ?? 0
  if (waiting === 0) return null
  const seconds = index.waiting_seconds ?? 0
  const since = seconds >= 120 ? ` for about ${Math.round(seconds / 60)} minutes` : ''
  const failing = index.failing ?? 0
  return (
    `${plural(waiting, 'text')} you changed ${waiting === 1 ? 'is' : 'are'} still being read in${since}; ` +
    `what ${waiting === 1 ? 'it says' : 'they say'} may be missing here for now.` +
    (failing > 0 ? ` ${plural(failing, 'text')} could not be read and will be tried again.` : '')
  )
}

const keptLabel = (kept: KeptContext) =>
  `Version ${kept.version_number}, kept ${fullDate(kept.kept_at)}`

/**
 * The context now against the one a kept draft was written from: what was added, what
 * is no longer given and why, and what changed. Read only; nothing is sent or changed.
 */
export function ContextCompare({ scene }: { scene: PlanItem }) {
  const { scope } = useProject()
  const kept = useQuery({
    queryKey: scopedKey(scope, 'scene-context-kept', scene.id),
    queryFn: () => api.keptSceneContexts(scope, scene.id),
  })
  const [chosen, setChosen] = useState('')
  const comparison = useQuery({
    queryKey: scopedKey(scope, 'scene-context-compare', scene.id, chosen),
    queryFn: () => api.compareSceneContext(scope, scene.id, chosen),
    enabled: chosen !== '',
  })
  const versions = kept.data ?? []
  if (kept.isPending) return null
  if (kept.error) return <ErrorNotice error={kept.error} prefix="Could not find the kept drafts" />
  if (versions.length === 0)
    return (
      <p className="muted small">
        When you keep a draft of this scene, you can compare what the assistant used for it with
        what it would use now.
      </p>
    )
  const version = versions.find((item) => item.manifest_id === chosen)
  return (
    <section className="stack tight context-compare" aria-label="Compare with a kept draft">
      <label className="field">
        <span>Compare with what a kept draft was written from</span>
        <select value={chosen} onChange={(event) => setChosen(event.target.value)}>
          <option value="">Don’t compare</option>
          {versions.map((item) => (
            <option key={item.manifest_id} value={item.manifest_id}>
              {keptLabel(item)}
            </option>
          ))}
        </select>
      </label>
      {chosen && comparison.isPending && <Loading>Comparing…</Loading>}
      <ErrorNotice error={comparison.error} prefix="Could not compare" />
      {comparison.data && version && <ComparisonView data={comparison.data} version={version} />}
    </section>
  )
}

function ComparisonView({ data, version }: { data: ContextComparison; version: KeptContext }) {
  const name = `version ${version.version_number}`
  const counts = [
    data.added.length && `${data.added.length} added`,
    data.removed.length && `${data.removed.length} no longer given`,
    data.changed.length && `${data.changed.length} changed`,
  ].filter(Boolean)
  return (
    <div className="stack tight compare-result" aria-live="polite">
      <p>
        {data.same || counts.length === 0 ? (
          <strong>Nothing has changed since {name} was drafted.</strong>
        ) : (
          <>
            <strong>Since {name} was drafted: {counts.join(', ')}</strong>
            {data.unchanged > 0 ? `; ${plural(data.unchanged, 'thing')} the same.` : '.'}
          </>
        )}{' '}
        <span className="muted small">
          About {data.words_then.toLocaleString()} words then, {data.words_now.toLocaleString()} now.
        </span>
      </p>
      {data.rules_changed && (
        <p className="muted small">The way the context is put together has changed since then too.</p>
      )}
      {data.added.length > 0 && (
        <div className="stack tight">
          <ul className="rows">
            {data.added.map((item) => (
              <li className="row context-row compare-added" key={item.key}>
                <span className="grow">
                  <span className="compare-mark">Added</span> <span className="context-text">{item.text}</span>
                  <span className="muted small context-reason">
                    {item.section_title} · {item.reason}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.removed.length > 0 && (
        <div className="stack tight">
          <ul className="rows">
            {data.removed.map((item) => (
              <li className="row context-row compare-removed" key={item.key}>
                <span className="grow">
                  <span className="compare-mark">Not given now</span>{' '}
                  <span className="context-text">
                    {item.text ?? item.now?.text ?? `A passage of your writing. ${item.reason}`}
                  </span>
                  <span className="muted small context-reason">
                    {item.section_title ? `${item.section_title} · ` : ''}
                    {item.left_out ? NOW_LEFT_OUT[item.left_out] : 'No longer in the story, or no longer about this scene'}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.changed.length > 0 && (
        <div className="stack tight">
          <ul className="rows">
            {data.changed.map((change) => (
              <li className="row context-row compare-changed" key={change.item.key}>
                <span className="grow">
                  <span className="compare-mark">Changed</span>{' '}
                  <span className="context-text">{change.item.text}</span>
                  {change.what.includes('words') && change.text_then && (
                    <span className="small context-was">
                      <span className="muted">Before: {change.text_then}</span>
                    </span>
                  )}
                  <span className="muted small context-reason">
                    What is different: {change.what.map((what) => WHAT_CHANGED[what] ?? what).join(', ')}
                    {change.what.includes('reason') ? ` · was “${change.reason_then}”` : ''}
                    {change.what.includes('section') && change.section_title_then
                      ? ` · was under ${change.section_title_then}`
                      : ''}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function sectionsOf(items: ContextItem[]): { title: string; items: ContextItem[] }[] {
  const groups: { title: string; items: ContextItem[] }[] = []
  for (const item of items) {
    const last = groups[groups.length - 1]
    if (last && last.title === item.section_title) last.items.push(item)
    else groups.push({ title: item.section_title, items: [item] })
  }
  return groups
}

/**
 * What the assistant would be given to write a scene: every item with its reason, the
 * length, and the author's say over each one. Nothing here calls a model.
 */
export function SceneContextDialog({ scene, onClose }: { scene: PlanItem; onClose: () => void }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const invalidate = useInvalidateProject(scope)
  const context = useQuery({
    queryKey: scopedKey(scope, 'scene-context', scene.id),
    queryFn: () => api.sceneContext(scope, scene.id),
    // While texts wait to be read in (CTX-005), look again shortly, so what they say
    // appears here by itself once it is in.
    refetchInterval: (query) => (query.state.data?.index?.waiting ? WAITING_EVERY : false),
  })
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildError, setRebuildError] = useState<unknown>(null)
  const save = useCanonWrite<SceneContextData, ContextSettings>((expectedRevision, settings) =>
    api.setSceneContext(scope, scene.id, settings, expectedRevision),
  )
  const data = context.data
  const settings = {
    pins: data?.settings.pins ?? [],
    excludes: data?.settings.excludes ?? [],
    budget: data?.settings.budget ?? null,
    include_secrets: data?.settings.include_secrets ?? false,
  }
  const change = (next: Partial<ContextSettings>) => void save.submit({ ...settings, ...next })
  const pin = (key: string) =>
    change({ pins: [...settings.pins.filter((k) => k !== key), key], excludes: settings.excludes.filter((k) => k !== key) })
  const exclude = (key: string) =>
    change({ excludes: [...settings.excludes.filter((k) => k !== key), key], pins: settings.pins.filter((k) => k !== key) })
  const release = (key: string) =>
    change({ pins: settings.pins.filter((k) => k !== key), excludes: settings.excludes.filter((k) => k !== key) })

  const pov = data?.point_of_view ? (entries.names.get(data.point_of_view) ?? 'the viewpoint character') : null
  const busy = save.pending || context.isFetching

  const controls = (item: ContextItem, left?: string) => {
    if (!canWrite) return null
    if (item.hard)
      return <span className="muted small">Always given</span>
    if (left === 'excluded')
      return (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => release(item.key)}>
          Use it again
        </Button>
      )
    if (left)
      return (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => pin(item.key)}>
          Use it
        </Button>
      )
    // Already given: the one thing to say is "not this". An item the author asked to
    // always use can be returned to the ordinary choice.
    return (
      <span className="row-actions">
        {item.pinned && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => release(item.key)}>
            Stop always using
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => exclude(item.key)}>
          Don’t use
        </Button>
      </span>
    )
  }

  const row = (item: ContextItem, left?: string) => (
    <li className="row context-row" key={item.key}>
      <span className="grow">
        <span className="context-text">{item.text}</span>
        <span className="muted small context-reason">
          {item.reason} · {plural(item.words, 'word')}
          {item.pinned ? ' · always used' : ''}
        </span>
        <RankMarks item={item} />
        {item.picture && (
          <span className="small context-reason">
            <Link to={`/projects/${scope.projectId}/pictures/${item.picture.asset_id}`}>
              Open the picture
            </Link>{' '}
            to describe it or change how it is used.
          </span>
        )}
      </span>
      {controls(item, left)}
    </li>
  )

  const leftGroups = Object.keys(LEFT_OUT)
    .map((reason) => ({ reason, items: (data?.left_out ?? []).filter((o) => o.reason === reason).map((o) => o.item) }))
    .filter((group) => group.items.length > 0)

  return (
    <Dialog title={`What the assistant would use for “${scene.title}”`} onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          This is what the assistant would be given to write this scene, worked out from your
          story bible, your plan and your writing. Each thing says why it is here. Nothing is sent
          until you ask for a draft.
        </p>
        {context.isPending && <Loading>Working it out…</Loading>}
        <ErrorNotice error={context.error} prefix="Could not work it out" />
        {data && (
          <>
            <div className="context-summary">
              <p>
                <strong>About {data.words.toLocaleString()} words</strong> of the{' '}
                {data.budget.toLocaleString()} you allow
                {pov ? `, seen through ${pov}’s eyes` : ', with no point of view chosen'}.
              </p>
              <label className="field">
                <span>How much to give</span>
                <select
                  value={String(settings.budget ?? data.budget)}
                  disabled={!canWrite || busy}
                  onChange={(event) => change({ budget: Number(event.target.value) })}
                >
                  {!BUDGETS.some((item) => item.value === (settings.budget ?? data.budget)) && (
                    <option value={String(settings.budget ?? data.budget)}>
                      About {(settings.budget ?? data.budget).toLocaleString()} words
                    </option>
                  )}
                  {BUDGETS.map((item) => (
                    <option key={item.value} value={String(item.value)}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              {pov && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={settings.include_secrets}
                    disabled={!canWrite || busy}
                    onChange={(event) => change({ include_secrets: event.target.checked })}
                  />
                  <span>
                    Also give the secrets {pov} does not know, marked as yours only
                    <span className="hint small muted" style={{ display: 'block' }}>
                      Useful for foreshadowing. The assistant is told never to let {pov} know them.
                    </span>
                  </span>
                </label>
              )}
            </div>
            <WriteProblem write={save} scope={scope} />
            <ContextCompare scene={scene} />

            {data.index && (data.index.texts > 0 || data.index.behind.length > 0) && (
              <p className="notice plain small">
                <span>
                  {data.index.behind.length === 0
                    ? `Your writing: ${plural(data.index.texts, 'text')} remembered${data.index.semantic ? ', searchable by meaning' : ', found by the names they use'}.`
                    : `Not yet remembered: ${data.index.behind.join(', ')}.`}
                </span>
                {canWrite && (
                  <Button
                    size="sm"
                    disabled={rebuilding}
                    onClick={async () => {
                      setRebuilding(true)
                      setRebuildError(null)
                      try {
                        await api.rebuildWritingIndex(scope)
                        await invalidate()
                      } catch (failure) {
                        setRebuildError(failure)
                      } finally {
                        setRebuilding(false)
                      }
                    }}
                  >
                    {rebuilding ? 'Reading your writing again…' : 'Read your writing again'}
                  </Button>
                )}
              </p>
            )}
            <ErrorNotice error={rebuildError} />
            {data.index && waitingWords(data.index) && (
              <p className="notice plain small" role="status">
                {waitingWords(data.index)}
              </p>
            )}

            {sectionsOf(data.items).map((group) => (
              <section key={group.title} className="stack tight context-section" aria-label={group.title}>
                <h3>{group.title}</h3>
                <ul className="rows">{group.items.map((item) => row(item))}</ul>
              </section>
            ))}

            {leftGroups.length > 0 && (
              <section className="stack tight context-section" aria-label="Left out">
                <h3>Left out</h3>
                {leftGroups.map((group) => (
                  <div key={group.reason} className="stack tight">
                    <p className="label">{LEFT_OUT[group.reason]}</p>
                    <ul className="rows">{group.items.map((item) => row(item, group.reason))}</ul>
                  </div>
                ))}
              </section>
            )}

            <SceneSummaryPanel scene={scene} />

            <details className="more">
              <summary>Technical details: exactly what would be given</summary>
              <pre tabIndex={0}>{data.rendered}</pre>
              <p className="muted small">
                Context rules {data.policy_version} · checksum {data.checksum.slice(0, 12)}
              </p>
            </details>
          </>
        )}
        <div className="actions end">
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
