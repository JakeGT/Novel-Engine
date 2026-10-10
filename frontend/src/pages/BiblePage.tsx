import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type AssetLink, type CanonEntity } from '../api/client'
import { Icon } from '../components/icons'
import { Portrait } from '../components/Pictures'
import { Badge, Button, EmptyState, ErrorNotice, Loading, ReadOnlyNotice } from '../components/ui'
import { isPicture } from '../lib/pictures'
import { plural, typeLabel, typePlural, valueText } from '../lib/words'
import { ENTRY_LIMIT, entryPath, scopedKey, useEntries, useProject } from '../project'

function subtitle(entity: CanonEntity): string {
  const aliases = entity.aliases ?? []
  if (aliases.length > 0) return `also ${aliases.join(', ')}`
  const first = Object.values(entity.attributes ?? {})[0]
  return first === undefined ? '' : valueText(first)
}

/** Everything in the story bible, browsable by type and searchable by any name. */
export function BiblePage() {
  const { scope, canWrite, newEntry } = useProject()
  const [params, setParams] = useSearchParams()
  const type = params.get('type') ?? ''
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
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

  // One request for every picture link in the project; the newest per entry is its portrait.
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', 'all'),
    queryFn: () => api.listLinks(scope, { include_retired: false }),
  })
  const portraits = new Map<string, AssetLink>()
  for (const link of links.data?.items ?? []) {
    const current = portraits.get(link.target_record_id)
    if (isPicture(link) && (!current || link.created_revision > current.created_revision)) {
      portraits.set(link.target_record_id, link)
    }
  }

  const visible = entries.items.filter((item) => showArchived || item.lifecycle === 'active')
  const counts = new Map<string, number>()
  for (const item of visible) counts.set(item.type, (counts.get(item.type) ?? 0) + 1)
  const types = [...new Set([...counts.keys(), ...(type ? [type] : [])])].sort((a, b) =>
    typePlural(a).localeCompare(typePlural(b)),
  )
  const items = term
    ? (found.data?.items ?? [])
    : visible
        .filter((item) => !type || item.type === type)
        .sort((a, b) => a.name.localeCompare(b.name))
  const loading = term ? found.isPending : entries.loading
  const error = term ? found.error : entries.error
  const setType = (next: string) => setParams(next ? { type: next } : {}, { replace: true })
  const empty = !entries.loading && entries.items.length === 0

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{type ? typePlural(type) : 'Story bible'}</h1>
          <p className="lede">
            {type
              ? `Every ${typeLabel(type).toLowerCase()} in your story.`
              : 'Everyone and everything in your story, in one place.'}
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
        {!empty && (
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
        )}

        {!empty && (
          <div className="chips" role="group" aria-label="Filter by type">
            <button type="button" className="chip" aria-pressed={!type} onClick={() => setType('')}>
              Everything <span className="count">{visible.length}</span>
            </button>
            {types.map((item) => (
              <button
                key={item}
                type="button"
                className="chip"
                aria-pressed={type === item}
                onClick={() => setType(item)}
              >
                {typePlural(item)} <span className="count">{counts.get(item) ?? 0}</span>
              </button>
            ))}
          </div>
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

        {!loading && !empty && (
          <p className="muted small" role="status">
            {term
              ? `${plural(items.length, 'match', 'matches')} for “${term}”`
              : plural(items.length, 'entry', 'entries')}
            {!term && entries.truncated
              ? ` shown — only the first ${ENTRY_LIMIT} are loaded, so search to find the rest`
              : ''}
          </p>
        )}

        {!loading && !empty && items.length === 0 && (
          <EmptyState icon="search" title="Nothing matches">
            {term ? 'Try a different name, or clear the search.' : 'There is nothing of this type yet.'}
          </EmptyState>
        )}

        <ul className="entry-grid">
          {items.map((entity) => (
            <li key={entity.id}>
              <Link
                className={`entry-card ${entity.lifecycle === 'active' ? '' : 'archived'}`}
                to={entryPath(scope, entity.id)}
              >
                <Portrait entity={entity} link={portraits.get(entity.id)} />
                <span className="entry-text">
                  <span className="entry-name">{entity.name}</span>
                  <span className="entry-sub">
                    {[type ? '' : typeLabel(entity.type), subtitle(entity)].filter(Boolean).join(' · ') ||
                      typeLabel(entity.type)}
                  </span>
                </span>
                {entity.lifecycle !== 'active' && <Badge tone="danger">Archived</Badge>}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
