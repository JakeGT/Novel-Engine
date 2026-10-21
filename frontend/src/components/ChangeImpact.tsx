import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Impact, type ImpactDependent } from '../api/client'
import { scenePath } from '../lib/studio'
import { scopedKey, useProject } from '../project'
import { Button, Loading } from './ui'

/** What each kind of dependent is called, in the author's words. */
const KIND: Record<ImpactDependent['kind'], string> = {
  scene: 'Scene',
  plan: 'Part of the plan',
  thread: 'Thread',
  rule: 'Story rule',
  draft: 'Kept drafts',
  belief: 'What they believe',
  event: 'Event',
  picture: 'Picture',
  finding: 'In the studio',
  scene_text: 'Scene’s written text',
  text: 'Your writing',
}

// A long list is cut here, with a button that says how many more there are (V5).
const SHOWN = 8

function where(projectId: string, item: ImpactDependent): string | null {
  switch (item.kind) {
    case 'scene':
    case 'plan':
    case 'thread':
      return item.plan_id ? `/projects/${projectId}/plan#plan-${item.plan_id}` : null
    case 'rule':
      return `/projects/${projectId}/rules`
    case 'draft':
    case 'finding':
      return item.plan_id ? scenePath(projectId, item.plan_id) : null
    case 'belief':
      // The character who holds the beliefs: their entry lists what they believe.
      return `/projects/${projectId}/entries/${encodeURIComponent(item.id)}`
    case 'event':
      return `/projects/${projectId}/timeline`
    case 'picture':
      return `/projects/${projectId}/pictures/${encodeURIComponent(item.id)}`
    case 'scene_text':
      if (!item.plan_id) return null
      return scenePath(
        projectId,
        item.plan_id,
        item.version_id && item.start != null && item.end != null
          ? { versionId: item.version_id, start: item.start, end: item.end }
          : null,
      )
    case 'text':
      return item.source_id
        ? `/projects/${projectId}/texts/${item.source_id}${item.start != null && item.end != null ? `#at-${item.start}-${item.end}` : ''}`
        : null
  }
}

function Dependents({
  label,
  hint,
  items,
  links,
}: {
  label: string
  hint: string
  items: ImpactDependent[]
  links: boolean
}) {
  const { scope } = useProject()
  const [all, setAll] = useState(false)
  if (items.length === 0) return null
  const shown = all ? items : items.slice(0, SHOWN)
  return (
    <section className="impact-group" aria-label={label}>
      <h3 className="impact-title">
        {label} <span className="muted">· {items.length}</span>
      </h3>
      <p className="hint small">{hint}</p>
      <ul className="rows">
        {shown.map((item) => {
          const to = links ? where(scope.projectId, item) : null
          return (
            <li className="row impact-row" key={`${item.kind}:${item.id}`}>
              <span className="impact-kind">{KIND[item.kind]}</span>
              <span className="grow">
                {to ? <Link to={to}>{item.title}</Link> : <strong>{item.title}</strong>}
                <span className="impact-reasons small">{item.reasons.join(' · ')}</span>
              </span>
            </li>
          )
        })}
      </ul>
      {items.length > SHOWN && (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
            {all ? `Show the first ${SHOWN} only` : `Show all ${items.length}`}
          </Button>
        </div>
      )}
    </section>
  )
}

/** The answer itself: affected, may be affected, and what was not checked. */
export function ImpactView({ impact, links = true }: { impact: Impact; links?: boolean }) {
  const nothing = impact.affected.length === 0 && impact.may_be_affected.length === 0
  return (
    <div className="impact-body">
      {nothing && (
        <p>
          {impact.about.length === 0
            ? 'Every change is skipped, so nothing in your story is touched.'
            : 'Nothing in your plan, story rules, kept drafts, beliefs, events, pictures or studio checks depends on it, and none of your writing names it.'}
        </p>
      )}
      <Dependents
        label="Affected"
        hint="Your plan, rules, kept drafts, what characters believe, events, pictures or the studio’s checks name what changes."
        items={impact.affected}
        links={links}
      />
      <Dependents
        label="May be affected"
        hint="Your writing mentions it by name. Whether it still reads right is for you to judge."
        items={impact.may_be_affected}
        links={links}
      />
      {/* Always shown: an answer from an incomplete index must not pass for the whole story. */}
      <div className="impact-group">
        <h3 className="impact-title">Not checked</h3>
        <ul className="impact-gaps small">
          {impact.not_checked.map((gap) => (
            <li key={gap}>{gap}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/** "What this change touches", for a set of suggested changes on the review screen. */
export function MutationImpact({ mutationId, version }: { mutationId: string; version: string }) {
  const { scope } = useProject()
  const impact = useQuery({
    // Worked out again whenever a change is kept, skipped or edited.
    queryKey: scopedKey(scope, 'mutation-impact', mutationId, version),
    queryFn: () => api.mutationImpact(scope, mutationId),
    retry: false,
  })
  return (
    <section aria-labelledby="impact-heading" className="card impact">
      <div className="card-head">
        <div>
          <h2 id="impact-heading">What this change touches</h2>
          <p className="muted small">
            Worked out from your plan, story rules, kept drafts, beliefs, events, pictures, the
            studio’s checks and the names found in your writing, for the changes you are keeping.
            The assistant is not asked.
          </p>
        </div>
      </div>
      {impact.isPending && <Loading>Looking for what depends on it…</Loading>}
      {impact.isError && (
        <p className="muted small">What this change touches could not be worked out just now. It does not stop you adding it.</p>
      )}
      {impact.data && <ImpactView impact={impact.data} />}
    </section>
  )
}

/** The same answer for one entry, beside its edit form: collapsed, so the form stays first. */
export function EntryImpact({ entityId, name }: { entityId: string; name: string }) {
  const { scope } = useProject()
  // Worked out when the form opens (kept drafts' contexts are read in one go), so the
  // collapsed line can already say how much depends on this entry.
  const [open, setOpen] = useState(false)
  const impact = useQuery({
    queryKey: scopedKey(scope, 'entry-impact', entityId),
    queryFn: () => api.entryImpact(scope, entityId),
    retry: false,
  })
  const counts = impact.data
    ? [
        impact.data.affected.length ? `${impact.data.affected.length} affected` : '',
        impact.data.may_be_affected.length ? `${impact.data.may_be_affected.length} may be affected` : '',
      ].filter(Boolean)
    : []
  return (
    <details className="more impact compact" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        What changing {name} touches
        {impact.data && <span className="muted"> · {counts.length ? counts.join(', ') : 'nothing found'}</span>}
      </summary>
      {open && impact.isPending && <Loading>Looking for what depends on {name}…</Loading>}
      {impact.isError && <p className="muted small">This could not be worked out just now.</p>}
      {impact.data && (
        <>
          {counts.length > 0 && (
            <p className="hint small">Saving does not change these. Follow them up after saving, from the plan, rules and texts.</p>
          )}
          <ImpactView impact={impact.data} links={false} />
        </>
      )}
    </details>
  )
}
