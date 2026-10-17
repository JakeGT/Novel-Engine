import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type ContextItem,
  type ContextSettings,
  type PlanItem,
  type SceneContext as SceneContextData,
} from '../api/client'
import { plural } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useInvalidateProject, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Loading, WriteProblem } from './ui'

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
