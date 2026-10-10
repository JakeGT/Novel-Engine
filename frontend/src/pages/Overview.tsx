import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Icon } from '../components/icons'
import { Button, ErrorNotice, Loading, TypeAvatar } from '../components/ui'
import { firstName, useSession } from '../lib/session'
import { timeAgo } from '../lib/time'
import { isOpenChange, originLabel, plural, typeLabel, typeLook, typePlural } from '../lib/words'
import { actorLabel, entryPath, scopedKey, useEntries, useProject } from '../project'

const STARTERS = [
  { type: 'Character', title: 'Add a character', text: 'Who is this story about?' },
  { type: 'Location', title: 'Add a place', text: 'Where does it happen?' },
  { type: undefined, title: 'Add something else', text: 'A faction, an object, an idea…' },
] as const

/** The project's front door: what is in the bible, what needs attention, what to do next. */
export function Overview() {
  const { scope, canWrite, newEntry, headRevision, userId } = useProject()
  const session = useSession()
  const entries = useEntries(scope)
  const base = `/projects/${scope.projectId}`

  const waiting = useQuery({
    queryKey: scopedKey(scope, 'mutations', 'open'),
    queryFn: () => api.listMutations(scope, { limit: 100 }),
  })
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions', 'recent'),
    queryFn: () => api.listRevisions(scope, { limit: 5 }),
  })
  const anyFact = useQuery({
    queryKey: scopedKey(scope, 'facts', 'any'),
    queryFn: () => api.listFacts(scope, { limit: 1 }),
  })

  const active = entries.items.filter((item) => item.lifecycle === 'active')
  const counts = new Map<string, number>()
  for (const item of active) counts.set(item.type, (counts.get(item.type) ?? 0) + 1)
  const types = [...counts.keys()].sort((a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0))
  const awaiting = (waiting.data?.items ?? []).filter((item) => isOpenChange(item.status)).length
  const hasFacts = (anyFact.data?.items.length ?? 0) > 0
  const name = firstName(session.data)

  if (entries.loading) return <Loading>Opening your story bible…</Loading>
  if (entries.error) return <ErrorNotice error={entries.error} prefix="Could not open the story bible" />

  if (active.length === 0) {
    return (
      <div className="section-gap">
        <section className="hero-card">
          <span className="eyebrow">A fresh start</span>
          <h1>{name ? `Let’s build your story bible, ${name}.` : 'Let’s build your story bible.'}</h1>
          <p className="lede">
            The story bible is where your world lives: who everyone is, how they are connected, and
            what is true. Start with a single name — you can add the rest whenever you know it.
          </p>
          {canWrite ? (
            <div className="tiles" style={{ marginTop: '1.5rem' }}>
              {STARTERS.map((starter) => {
                const look = typeLook(starter.type ?? 'Concept')
                return (
                  <button
                    key={starter.title}
                    type="button"
                    className="tile"
                    style={{ textAlign: 'left', font: 'inherit', cursor: 'pointer' }}
                    onClick={() => newEntry?.(starter.type)}
                  >
                    <span className={`avatar tint-${look.tint}`} aria-hidden="true">
                      <Icon name={starter.type ? look.icon : 'plus'} />
                    </span>
                    <strong>{starter.title}</strong>
                    <span className="muted small">{starter.text}</span>
                  </button>
                )
              })}
            </div>
          ) : (
            <p className="notice plain" role="note" style={{ marginTop: '1.25rem' }}>
              Nothing has been added yet, and you have read-only access to this project.
            </p>
          )}
        </section>

        <section aria-labelledby="how-heading" className="stack">
          <h2 id="how-heading">How it works</h2>
          <ol className="steps">
            <li className="step">
              <strong>Add entries</strong>
              <span className="muted">Characters, places, factions, objects — anything your story needs to remember.</span>
            </li>
            <li className="step">
              <strong>Connect them with facts</strong>
              <span className="muted">Build simple sentences like “Mara is a member of the Harbor Guild”.</span>
            </li>
            <li className="step">
              <strong>Stay in control</strong>
              <span className="muted">Bigger changes wait for your review, and every save is a version you can return to.</span>
            </li>
          </ol>
        </section>
      </div>
    )
  }

  const recent = revisions.data?.items ?? []
  const newest = [...active].sort((a, b) => b.created_revision - a.created_revision).slice(0, 6)

  return (
    <div className="section-gap">
      <section className="hero-card">
        <span className="eyebrow">Story bible · version {headRevision}</span>
        <h1>{name ? `Welcome back, ${name}.` : 'Welcome back.'}</h1>
        <p className="lede">
          Your story bible holds {plural(active.length, 'entry', 'entries')}
          {entries.truncated ? ' or more' : ''} across {plural(types.length, 'type')}.
          {awaiting > 0 ? ` ${plural(awaiting, 'suggested change')} ${awaiting === 1 ? 'is' : 'are'} waiting for you.` : ''}
        </p>
        <div className="actions" style={{ marginTop: '1.25rem' }}>
          {canWrite && (
            <Button variant="primary" icon="plus" onClick={() => newEntry?.()}>
              New entry
            </Button>
          )}
          <Link className="btn" to={`${base}/bible`}>
            Browse the story bible
          </Link>
          {awaiting > 0 && (
            <Link className="btn" to={`${base}/review`}>
              <Icon name="inbox" size={17} /> Review {plural(awaiting, 'change')}
            </Link>
          )}
        </div>
      </section>

      <section aria-label="Entries by type" className="tiles">
        {types.map((type) => (
          <Link key={type} className="tile" to={`${base}/bible?type=${encodeURIComponent(type)}`}>
            <TypeAvatar type={type} />
            <span className="num">{counts.get(type)}</span>
            <span className="tile-label">
              {counts.get(type) === 1 ? typeLabel(type) : typePlural(type)}
            </span>
          </Link>
        ))}
      </section>

      {!hasFacts && anyFact.isSuccess && canWrite && (
        <section className="card quiet" aria-labelledby="next-heading">
          <div className="card-head">
            <div>
              <h2 id="next-heading">Next: connect your entries</h2>
              <p className="muted">
                Open any entry and add a fact — who knows whom, who owns what, where someone is.
                Facts are what let the bible keep your story straight.
              </p>
            </div>
          </div>
          <Link className="btn" to={entryPath(scope, newest[0].id)}>
            Open {newest[0].name} <Icon name="arrowRight" size={16} />
          </Link>
        </section>
      )}

      <div className="two-col">
        <section className="card" aria-labelledby="recent-entries-heading">
          <div className="card-head">
            <h2 id="recent-entries-heading">Recently added</h2>
            <Link to={`${base}/bible`} className="small">
              See all
            </Link>
          </div>
          <ul className="rows">
            {newest.map((item) => (
              <li className="row" key={item.id}>
                <TypeAvatar type={item.type} name={item.name} size="sm" />
                <Link className="grow" to={entryPath(scope, item.id)}>
                  {item.name}
                </Link>
                <span className="muted small">{typeLabel(item.type)}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card" aria-labelledby="recent-changes-heading">
          <div className="card-head">
            <h2 id="recent-changes-heading">Latest versions</h2>
            <Link to={`${base}/history`} className="small">
              Full history
            </Link>
          </div>
          <ErrorNotice error={revisions.error} prefix="Could not load history" />
          <ol className="timeline">
            {recent.map((revision) => {
              const who =
                revision.origin && revision.origin !== 'author'
                  ? originLabel(revision.origin)
                  : actorLabel(revision.actor_user_id, userId) || 'an author'
              return (
                <li key={revision.commit_id}>
                  <strong>Version {revision.revision}</strong>{' '}
                  <span className="muted">
                    · {plural(revision.operation_count, 'change')} by {who}
                  </span>
                  <div className="when">{timeAgo(revision.created_at)}</div>
                </li>
              )
            })}
          </ol>
        </section>
      </div>
    </div>
  )
}
