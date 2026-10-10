import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { OpenConversations } from '../components/Conversations'
import { Icon } from '../components/icons'
import { Portrait } from '../components/Pictures'
import { Button, ErrorNotice, Loading, TypeAvatar } from '../components/ui'
import { usePortraits } from '../lib/pictures'
import { firstName, useSession } from '../lib/session'
import { timeAgo } from '../lib/time'
import { isOpenChange, originLabel, plural, typeLabel, typeLook, typePlural } from '../lib/words'
import { actorLabel, entryPath, scopedKey, useEntries, useProject } from '../project'

const STARTERS = [
  {
    type: undefined,
    title: 'Describe your story',
    text: 'Write a paragraph. The assistant drafts the people, places and links for you to review.',
  },
  { type: 'Character', title: 'Add a character', text: 'Who is this story about?' },
  { type: 'Location', title: 'Add a place', text: 'Where does it happen?' },
] as const

/** The project's front door: what is in the bible, what needs attention, what to do next. */
export function Overview() {
  const { scope, canWrite, canAdmin, access, newEntry, fillIn, headRevision, userId } = useProject()
  const session = useSession()
  const entries = useEntries(scope)
  const portraits = usePortraits(scope)
  const base = `/projects/${scope.projectId}`

  const waiting = useQuery({
    queryKey: scopedKey(scope, 'mutations', 'open'),
    queryFn: () => api.listMutations(scope, { limit: 100 }),
  })
  const revisions = useQuery({
    queryKey: scopedKey(scope, 'revisions', 'recent'),
    queryFn: () => api.listRevisions(scope, { limit: 5 }),
  })
  const todo = useQuery({
    queryKey: scopedKey(scope, 'entities', 'needs-detail'),
    queryFn: () => api.needsDetail(scope),
  })
  // Only an owner can connect a key, and only a hosted site keeps keys per workspace.
  const keys = useQuery({
    queryKey: ['credentials', scope.workspaceId],
    queryFn: () => api.listCredentials(scope.workspaceId as string),
    enabled: Boolean(access?.hosted && canAdmin && scope.workspaceId),
  })
  const noKey = keys.isSuccess && !keys.data.some((key) => key.status === 'active')
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
            what is true. Describe it in your own words and the assistant does the filing: it
            drafts the entries, links them together, and shows you everything before it is saved.
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
                      <Icon name={starter.type ? look.icon : 'spark'} />
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

        <OpenConversations />

        {noKey && (
          <section className="card quiet" aria-labelledby="key-heading">
            <div className="card-head">
              <div>
                <h2 id="key-heading">Connect the assistant</h2>
                <p className="muted">
                  The assistant drafts entries and links from what you tell it, and it runs on your
                  own OpenRouter key so you control the cost. It takes a minute to connect. Until
                  then, everything can be filled in by hand.
                </p>
              </div>
            </div>
            <Link className="btn" to={`${base}/assistant`}>
              Connect an AI key
            </Link>
          </section>
        )}

        <section aria-labelledby="how-heading" className="stack">
          <h2 id="how-heading">How it works</h2>
          <ol className="steps">
            <li className="step">
              <strong>Describe it</strong>
              <span className="muted">Write about a character, a place, or the whole idea, the way you would tell a friend.</span>
            </li>
            <li className="step">
              <strong>Answer a few questions</strong>
              <span className="muted">The assistant asks what it needs, then drafts the entries and how they connect.</span>
            </li>
            <li className="step">
              <strong>Review and keep what’s right</strong>
              <span className="muted">Nothing is saved until you approve it, and every save is a version you can return to.</span>
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

      <OpenConversations />

      {(todo.data?.items.length ?? 0) > 0 && (
        <section className="card" aria-labelledby="todo-heading">
          <div className="card-head">
            <div>
              <h2 id="todo-heading">To fill in</h2>
              <p className="muted">
                These are just names so far — usually because something else mentioned them. Let
                the assistant ask you about each one.
              </p>
            </div>
            <span className="badge accent">{todo.data?.items.length}</span>
          </div>
          <ul className="rows">
            {(todo.data?.items ?? []).slice(0, 8).map((item) => (
              <li className="row" key={item.id}>
                <Portrait entity={item} link={portraits.get(item.id)} size="sm" />
                <Link className="grow" to={entryPath(scope, item.id)}>
                  {item.name}
                </Link>
                <span className="muted small">{typeLabel(item.type)}</span>
                {canWrite && (
                  <Button size="sm" icon="chat" aria-label={`Fill in ${item.name}`} onClick={() => fillIn?.(item)}>
                    Fill in
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

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
                <Portrait entity={item} link={portraits.get(item.id)} size="sm" />
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
