import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, type WhatIfAudit } from '../api/client'
import { Badge, ErrorNotice, Loading } from '../components/ui'
import { scenePath } from '../lib/studio'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'

const CHANGE: Record<string, { label: string; tone: 'ok' | 'neutral' | 'info' }> = {
  added: { label: 'Only in the what-if', tone: 'info' },
  removed: { label: 'Only in your story', tone: 'neutral' },
  changed: { label: 'Different', tone: 'neutral' },
}

const KIND: Record<string, string> = {
  entry: 'Entry',
  fact: 'Fact',
  event: 'Event',
  plan: 'Plan',
}

type Difference = WhatIfAudit['bible'][number]

function Differences({ items, empty }: { items: Difference[]; empty: string }) {
  const { scope } = useProject()
  if (items.length === 0) return <p className="muted small">{empty}</p>
  return (
    <ul className="difference-list">
      {items.map((item, index) => {
        const change = CHANGE[item.change] ?? CHANGE.changed
        return (
          <li key={`${item.kind}-${item.title}-${index}`}>
            <Badge tone={change.tone}>{change.label}</Badge>
            <span className="muted small">{KIND[item.kind] ?? 'Record'}</span>
            {item.plan_id ? (
              <Link to={`/projects/${scope.projectId}/plan#plan-${item.plan_id}`}>{item.title}</Link>
            ) : (
              <strong>{item.title}</strong>
            )}
            {item.detail && <span className="small">{item.detail}</span>}
          </li>
        )
      })}
    </ul>
  )
}

/**
 * An audit of what differs between a what-if and the main story (CON-011): the story
 * bible, the plan, the scenes' words (with how the what-if's version sounds against the
 * author's usual), and the checks found in one and not the other. No assistant is asked
 * and nothing is changed; bringing changes back is the what-if page's own action.
 */
export function WhatIfAuditPage() {
  const { branchId = '' } = useParams()
  const { scope } = useProject()
  const audit = useQuery({
    queryKey: scopedKey(scope, 'what-if-audit', branchId),
    queryFn: () => api.whatIfAudit(scope, branchId),
    retry: false,
  })
  const found = audit.data
  return (
    <div className="stack audit-page">
      <header className="stack tight">
        <p className="eyebrow">
          <Link to={`/projects/${scope.projectId}/what-ifs`}>What-ifs</Link> ·{' '}
          <Link to={`/projects/${scope.projectId}/what-ifs/${encodeURIComponent(branchId)}`}>
            {found ? `“${found.what_if_name}”` : 'This what-if'}
          </Link>{' '}
          · Differences
        </p>
        <h1>{found ? `How “${found.what_if_name}” differs from your story` : 'How this what-if differs'}</h1>
        <p className="lede">
          Worked out now from both, with no assistant. Nothing in either is changed.
        </p>
      </header>
      {audit.isPending && <Loading>Comparing the what-if with your story…</Loading>}
      <ErrorNotice error={audit.error} prefix="Could not compare this what-if" />
      {found && found.same && (
        <p role="status">Nothing differs yet: the what-if still matches your story.</p>
      )}
      {found && !found.same && (
        <>
          <p role="status" className="audit-summary">
            <span>{plural(found.bible.length, 'difference')} in the story bible</span>
            <span>{plural(found.plan.length, 'difference')} in the plan</span>
            <span>{plural(found.scenes.length, 'scene')} written differently</span>
            <span>{plural(found.new_findings.length, 'check')} found only in the what-if</span>
          </p>
          <section className="audit-extra" aria-labelledby="checks-heading">
            <h2 id="checks-heading">Checks</h2>
            <h3 className="audit-group-title">Found in the what-if, not in your story</h3>
            {found.new_findings.length === 0 ? (
              <p className="muted small">The what-if brings no new problems the checks can see.</p>
            ) : (
              <ul className="findings">
                {found.new_findings.map((message) => (
                  <li className="finding warning" key={message}>
                    <Badge tone="warn">Worth checking</Badge>
                    <p>{message}</p>
                  </li>
                ))}
              </ul>
            )}
            <h3 className="audit-group-title">Found in your story, not in the what-if</h3>
            {found.gone_findings.length === 0 ? (
              <p className="muted small">None.</p>
            ) : (
              <ul className="findings">
                {found.gone_findings.map((message) => (
                  <li className="finding" key={message}>
                    <Badge tone="ok">Not in the what-if</Badge>
                    <p>{message}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="audit-extra" aria-labelledby="scenes-heading">
            <h2 id="scenes-heading">Scenes written differently</h2>
            {found.scenes.length === 0 ? (
              <p className="muted small">Every scene still has the same words as your story.</p>
            ) : (
              <ul className="rows">
                {found.scenes.map((scene) => (
                  <li className="row stack tight" key={scene.plan_id}>
                    <Link to={scenePath(scope.projectId, scene.plan_id)}>{scene.title}</Link>
                    <span className="small muted">
                      {scene.main_words.toLocaleString('en-GB')} words in your story,{' '}
                      {scene.what_if_words.toLocaleString('en-GB')} in the what-if
                    </span>
                    {scene.voice.map((line) => (
                      <span className="small" key={line}>
                        <Badge>Voice</Badge> {line}
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="audit-extra" aria-labelledby="bible-heading">
            <h2 id="bible-heading">The story bible</h2>
            <Differences items={found.bible} empty="The story bible is the same in both." />
          </section>
          <section className="audit-extra" aria-labelledby="plan-heading">
            <h2 id="plan-heading">The plan</h2>
            <Differences items={found.plan} empty="The plan is the same in both." />
          </section>
          {found.left_out.map((line) => (
            <p className="hint" key={line}>
              {line}
            </p>
          ))}
        </>
      )}
    </div>
  )
}
