import { useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Impact, type ImpactDependent } from '../api/client'

type ImpactAfter = NonNullable<Impact['after_change']>[number]
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

/** What each kind is called on the picture: plural, short. */
const KIND_MANY: Record<ImpactDependent['kind'], string> = {
  scene: 'Scenes',
  plan: 'Plan parts',
  thread: 'Threads',
  rule: 'Story rules',
  draft: 'Kept drafts',
  belief: 'Beliefs',
  event: 'Events',
  picture: 'Pictures',
  finding: 'Studio checks',
  scene_text: 'Scene texts',
  text: 'Your writing',
}

interface GraphGroup {
  kind: ImpactDependent['kind']
  affected: number
  maybe: number
}

/**
 * A small picture of what a change touches (CON-017): the change in the middle and each
 * kind of thing it touches around it, with how many. A solid line is "affected" (records
 * name it), a dashed one "may be affected" (writing mentions it), so the line's pattern
 * says it, not its colour. The lists below say the same in full, so the picture is never
 * the only way to read it.
 */
export function ImpactGraph({ impact }: { impact: Impact }) {
  const uid = useId()
  const groups = new Map<ImpactDependent['kind'], GraphGroup>()
  for (const item of [...impact.affected, ...impact.may_be_affected]) {
    const group = groups.get(item.kind) ?? { kind: item.kind, affected: 0, maybe: 0 }
    if (item.level === 'affected') group.affected += 1
    else group.maybe += 1
    groups.set(item.kind, group)
  }
  const nodes = [...groups.values()]
  if (nodes.length === 0) return null
  // The change on the left and what it touches in one column on the right, so the picture
  // stays readable at phone width however many kinds there are.
  const width = 340
  const row = 46
  const height = Math.max(120, nodes.length * row + 16)
  const cx = 52
  const cy = height / 2
  const nodeX = 186
  const nodeWidth = 148
  const summary = nodes
    .map((node) => {
      const parts = [node.affected ? `${node.affected} affected` : '', node.maybe ? `${node.maybe} may be affected` : '']
      return `${KIND_MANY[node.kind]}: ${parts.filter(Boolean).join(', ')}`
    })
    .join('; ')
  return (
    <figure className="impact-graph">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${uid}-title`} aria-describedby={`${uid}-desc`}>
        <title id={`${uid}-title`}>What this change touches, as a picture</title>
        <desc id={`${uid}-desc`}>{summary}</desc>
        {nodes.map((node, index) => {
          const y = 8 + row * index + row / 2
          const only = node.affected === 0
          return (
            <g key={node.kind} className={only ? 'impact-node maybe' : 'impact-node'}>
              <line x1={cx + 40} y1={cy} x2={nodeX} y2={y} strokeDasharray={only ? '5 4' : undefined} />
              <rect x={nodeX} y={y - 18} width={nodeWidth} height={36} rx={4} />
              <text x={nodeX + 10} y={y - 3}>
                {KIND_MANY[node.kind]}
              </text>
              <text x={nodeX + 10} y={y + 12} className="impact-node-count">
                {[node.affected ? `${node.affected} affected` : '', node.maybe ? `${node.maybe} may be` : '']
                  .filter(Boolean)
                  .join(' · ')}
              </text>
            </g>
          )
        })}
        <g className="impact-centre">
          <circle cx={cx} cy={cy} r={40} />
          <text x={cx} y={cy + 4} textAnchor="middle">
            The change
          </text>
        </g>
      </svg>
      <figcaption className="hint small">
        A solid line: your records name it. A dashed line: only your writing mentions it. Each is
        listed in full below.
      </figcaption>
    </figure>
  )
}

const AFTER_KIND: Record<ImpactAfter['kind'], string> = {
  condition: 'Scene conditions',
  reader: 'The reader’s guide',
}

/** How the plan's conditions and the reader's guide would read once the change is kept. */
function AfterTheChange({ impact, links }: { impact: Impact; links: boolean }) {
  const { scope } = useProject()
  if (!impact.scratch) return null
  const items = impact.after_change ?? []
  return (
    <section className="impact-group" aria-label="After the change">
      <h3 className="impact-title">
        After the change <span className="muted">· {items.length}</span>
      </h3>
      <p className="hint small">
        Worked out on a copy of your story with the change applied. Nothing is saved until you add
        the change.
      </p>
      {items.length === 0 ? (
        <p className="small">The plan’s conditions and the reader’s guide would read the same.</p>
      ) : (
        (['condition', 'reader'] as const).map((kind) => {
          const rows = items.filter((item) => item.kind === kind)
          if (rows.length === 0) return null
          return (
            <div key={kind} className="stack tight">
              <h4 className="impact-subtitle">{AFTER_KIND[kind]}</h4>
              <ul className="rows">
                {rows.map((item, index) => (
                  <li className="row impact-row" key={`${item.plan_id}:${index}`}>
                    <span className="grow">
                      {links && !item.plan_id.startsWith('tmp:') ? (
                        <Link
                          to={
                            kind === 'reader'
                              ? `/projects/${scope.projectId}/plan/${encodeURIComponent(item.plan_id)}/guide`
                              : `/projects/${scope.projectId}/plan#plan-${item.plan_id}`
                          }
                        >
                          {item.title}
                        </Link>
                      ) : (
                        <strong>{item.title}</strong>
                      )}
                      <span className="impact-reasons small">{item.words}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )
        })
      )}
    </section>
  )
}

/** The answer itself: affected, may be affected, and what was not checked. */
export function ImpactView({ impact, links = true }: { impact: Impact; links?: boolean }) {
  const nothing = impact.affected.length === 0 && impact.may_be_affected.length === 0
  const [picture, setPicture] = useState(false)
  return (
    <div className="impact-body">
      {!nothing && (
        <div>
          <Button size="sm" variant="ghost" aria-expanded={picture} onClick={() => setPicture(!picture)}>
            {picture ? 'Hide the picture' : 'Show it as a picture'}
          </Button>
        </div>
      )}
      {picture && <ImpactGraph impact={impact} />}
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
      <AfterTheChange impact={impact} links={links} />
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
