import { useRelations } from '../lib/relations'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type AssetLink, type CanonEntity, type Scope } from '../api/client'
import { EntryMarks } from '../components/EntryMarks'
import { FindConnections } from '../components/FindConnections'
import { Icon } from '../components/icons'
import { Portrait } from '../components/Pictures'
import { Badge, Button, EmptyState, ErrorNotice, Loading, ReadOnlyNotice, TypeAvatar } from '../components/ui'
import { byPriority, detailsOf } from '../lib/marks'
import { usePortraits } from '../lib/pictures'
import { plural, typeLabel, typePlural, valueText } from '../lib/words'
import { ENTRY_LIMIT, entryPath, scopedKey, useEntries, useProject } from '../project'

/** How many entries of a type the "Everything" view shows before asking. */
const FIRST = 5
const MORE = 10

function subtitle(entity: CanonEntity): string {
  const aliases = entity.aliases ?? []
  if (aliases.length > 0) return `also ${aliases.join(', ')}`
  const first = Object.values(detailsOf(entity.attributes))[0]
  return first === undefined ? '' : valueText(first)
}

function EntryList({
  items,
  scope,
  portraits,
  relations,
  showType,
}: {
  items: CanonEntity[]
  scope: Scope
  portraits: Map<string, AssetLink>
  /** How each entry is connected to the story's main entries, from the facts. */
  relations: Map<string, string>
  showType: boolean
}) {
  return (
    <ul className="entry-rows">
      {items.map((entity) => (
        <li key={entity.id}>
          <Link
            className={`entry-row ${entity.lifecycle === 'active' ? '' : 'archived'}`}
            to={entryPath(scope, entity.id)}
          >
            <Portrait entity={entity} link={portraits.get(entity.id)} size="sm" />
            <span className="entry-text">
              <span className="entry-name">{entity.name}</span>
              <span className="entry-sub">
                {[showType ? typeLabel(entity.type) : '', relations.get(entity.id), subtitle(entity)]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </span>
            <EntryMarks entity={entity} />
            {entity.lifecycle !== 'active' && <Badge tone="danger">Archived</Badge>}
          </Link>
        </li>
      ))}
    </ul>
  )
}

/**
 * The story bible. It opens on an index of its parts; "Everything" shows each type
 * a few entries at a time; a type's own page lists all of it. Entries are always
 * in order of how much they matter to the story, never alphabetical.
 */
export function BiblePage() {
  const { scope, canWrite, newEntry } = useProject()
  const [params] = useSearchParams()
  const type = params.get('type') ?? ''
  const everything = !type && params.get('view') === 'all'
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [shown, setShown] = useState<Record<string, number>>({})
  const term = search.trim()
  const entries = useEntries(scope)

  // Searching goes to the server, which also matches other names and is not capped.
  const found = useQuery({
    queryKey: scopedKey(scope, 'entities', 'search', type, term, showArchived),
    queryFn: () =>
      api.searchEntities(scope, {
        q: term,
        entity_type: type || undefined,
        include_retired: showArchived,
        limit: 100,
      }),
    enabled: term.length > 0,
  })
  const portraits = usePortraits(scope)
  const relations = useRelations(scope, entries.items)

  const visible = entries.items
    .filter((item) => showArchived || item.lifecycle === 'active')
    .sort(byPriority)
  const byType = new Map<string, CanonEntity[]>()
  for (const item of visible) byType.set(item.type, [...(byType.get(item.type) ?? []), item])
  // The parts with the most in them first: that is where the story is.
  const types = [...byType.keys()].sort(
    (a, b) => (byType.get(b)?.length ?? 0) - (byType.get(a)?.length ?? 0) || typePlural(a).localeCompare(typePlural(b)),
  )
  const loading = term ? found.isPending : entries.loading
  const error = term ? found.error : entries.error
  const empty = !entries.loading && entries.items.length === 0
  const base = `/projects/${scope.projectId}/bible`

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{type ? typePlural(type) : everything ? 'Everything' : 'Story bible'}</h1>
          <p className="lede">
            {type
              ? `Every ${typeLabel(type).toLowerCase()} in your story, the most important first.`
              : everything
                ? 'Each part of your story bible, a few entries at a time, the most important first.'
                : 'Everyone and everything in your story. Choose a part to open.'}
          </p>
        </div>
        {canWrite && (
          <Button variant="primary" icon="plus" onClick={() => newEntry?.(type || undefined)}>
            {type ? `New ${typeLabel(type).toLowerCase()}` : 'New entry'}
          </Button>
        )}
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {!empty && !entries.loading && (
          <>
            {/* On the index the list below is the navigation; one way in is enough. */}
            {(type || everything) && (
              <nav className="chips" aria-label="Parts of the story bible">
                <Link className="chip" to={base} aria-current={!type && !everything ? 'page' : undefined}>
                  Index
                </Link>
                <Link className="chip" to={`${base}?view=all`} aria-current={everything ? 'page' : undefined}>
                  Everything <span className="count">{visible.length}</span>
                </Link>
                {types.map((item) => (
                  <Link
                    key={item}
                    className="chip"
                    to={`${base}?type=${encodeURIComponent(item)}`}
                    aria-current={type === item ? 'page' : undefined}
                  >
                    {typePlural(item)} <span className="count">{byType.get(item)?.length ?? 0}</span>
                  </Link>
                ))}
              </nav>
            )}
            <form className="actions" role="search" onSubmit={(event) => event.preventDefault()}>
              <div className="search">
                <Icon name="search" />
                <input
                  type="search"
                  aria-label="Search entries"
                  placeholder="Search by name or nickname"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(event) => setShowArchived(event.target.checked)}
                />
                Show archived
              </label>
            </form>
          </>
        )}

        <ErrorNotice error={error} prefix="Could not load entries" />
        {loading && <Loading>Loading entries…</Loading>}

        {empty && (
          <EmptyState
            icon="book"
            title="Your story bible is empty"
            action={
              canWrite ? (
                <Button variant="primary" icon="plus" onClick={() => newEntry?.()}>
                  Add your first entry
                </Button>
              ) : undefined
            }
          >
            Start with one character or one place. All it needs is a name.
          </EmptyState>
        )}

        {!loading && !empty && entries.truncated && !term && (
          <p className="muted small" role="status">
            Only the first {ENTRY_LIMIT} entries are loaded, so search to find the rest.
          </p>
        )}

        {/* --- search results, whichever part is open --- */}
        {term && !loading && (
          <section aria-labelledby="results-heading" className="stack tight">
            <h2 id="results-heading" className="list-heading">
              {plural(found.data?.items.length ?? 0, 'match', 'matches')} for “{term}”
            </h2>
            {(found.data?.items.length ?? 0) === 0 ? (
              <EmptyState icon="search" title="Nothing matches">
                Try a different name, or clear the search.
              </EmptyState>
            ) : (
              <EntryList items={found.data?.items ?? []} scope={scope} portraits={portraits} relations={relations} showType={!type} />
            )}
          </section>
        )}

        {/* --- the index: a way in to each part --- */}
        {!term && !loading && !empty && !type && !everything && (
          <>
          <ul className="index">
            {types.map((item) => {
              const inType = byType.get(item) ?? []
              return (
                <li key={item}>
                  <Link
                    className="index-row"
                    to={`${base}?type=${encodeURIComponent(item)}`}
                    aria-label={`${typePlural(item)}, ${plural(inType.length, 'entry', 'entries')}`}
                  >
                    <TypeAvatar type={item} />
                    <span className="index-text">
                      <span className="index-name">{typePlural(item)}</span>
                      <span className="index-sub">
                        {inType
                          .slice(0, 3)
                          .map((entity) => entity.name)
                          .join(', ')}
                        {inType.length > 3 ? `, and ${inType.length - 3} more` : ''}
                      </span>
                    </span>
                    <span className="index-count">{inType.length}</span>
                    <Icon name="arrowRight" />
                  </Link>
                </li>
              )
            })}
            <li>
              <Link
                className="index-row"
                to={`${base}?view=all`}
                aria-label={`Everything, ${plural(visible.length, 'entry', 'entries')}`}
              >
                <TypeAvatar type="" />
                <span className="index-text">
                  <span className="index-name">Everything</span>
                  <span className="index-sub">Every part on one page, a few entries at a time</span>
                </span>
                <span className="index-count">{visible.length}</span>
                <Icon name="arrowRight" />
              </Link>
            </li>
          </ul>
          {canWrite && visible.filter((item) => item.lifecycle === 'active').length >= 2 && <FindConnections />}
          </>
        )}

        {/* --- everything: each type under its own heading, a few at a time --- */}
        {!term && !loading && everything &&
          types.map((item) => {
            const inType = byType.get(item) ?? []
            const count = shown[item] ?? FIRST
            const rest = inType.length - count
            return (
              <section key={item} aria-labelledby={`type-${item}`} className="stack tight">
                <h2 id={`type-${item}`} className="list-heading">
                  <Link to={`${base}?type=${encodeURIComponent(item)}`}>{typePlural(item)}</Link>
                  <span className="count">{inType.length}</span>
                </h2>
                <EntryList items={inType.slice(0, count)} scope={scope} portraits={portraits} relations={relations} showType={false} />
                {rest > 0 && (
                  <div>
                    <Button size="sm" onClick={() => setShown({ ...shown, [item]: count + MORE })}>
                      Show {Math.min(MORE, rest)} more {typePlural(item).toLowerCase()}
                    </Button>
                  </div>
                )}
              </section>
            )
          })}

        {/* --- one type: all of it --- */}
        {!term && !loading && type &&
          ((byType.get(type)?.length ?? 0) === 0 ? (
            <EmptyState icon="search" title="Nothing of this type yet">
              <Link to={base}>Back to the index</Link>
            </EmptyState>
          ) : (
            <EntryList items={byType.get(type) ?? []} scope={scope} portraits={portraits} relations={relations} showType={false} />
          ))}
      </div>
    </>
  )
}
