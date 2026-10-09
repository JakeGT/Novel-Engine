import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { EntityForm } from '../components/EntityForm'
import { ErrorNotice, LifecycleBadge, ReadOnlyNotice } from '../components/ui'
import { scopedKey, useProject } from '../project'

export function EntityList() {
  const { scope, canWrite } = useProject()
  const [type, setType] = useState('')
  const [search, setSearch] = useState('')
  const [includeRetired, setIncludeRetired] = useState(false)
  const term = search.trim()

  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })

  const entities = useQuery({
    queryKey: scopedKey(scope, 'entities', type, term, includeRetired),
    queryFn: () =>
      term
        ? api.searchEntities(scope, {
            q: term,
            entity_type: type || undefined,
            include_retired: includeRetired,
            limit: 100,
          })
        : api.listEntities(scope, {
            entity_type: type || undefined,
            include_retired: includeRetired,
            limit: 100,
          }),
  })

  const items = entities.data?.items ?? []

  return (
    <>
      <section className="panel" aria-labelledby="entities-heading">
        <h2 id="entities-heading">Entities</h2>
        <form className="filters" role="search" onSubmit={(event) => event.preventDefault()}>
          <label>
            Search
            <input
              type="search"
              value={search}
              placeholder="Name, slug, or alias"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label>
            Type
            <select value={type} onChange={(event) => setType(event.target.value)}>
              <option value="">All types</option>
              {(vocabulary.data?.entity_types ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={includeRetired}
              onChange={(event) => setIncludeRetired(event.target.checked)}
            />
            Include retired
          </label>
        </form>

        <ErrorNotice error={entities.error} prefix="Could not load entities" />
        <p className="muted" role="status">
          {entities.isLoading
            ? 'Loading entities…'
            : `${items.length} ${items.length === 1 ? 'entity' : 'entities'}`}
        </p>
        <ul className="list">
          {items.map((entity) => (
            <li key={entity.id}>
              <Link
                to={`/projects/${scope.projectId}/${entity.type === 'Character' ? 'characters' : 'entities'}/${entity.id}`}
              >
                {entity.name}
              </Link>{' '}
              <span className="muted">{entity.type}</span>{' '}
              {(entity.aliases ?? []).length > 0 && (
                <span className="muted">also {(entity.aliases ?? []).join(', ')}</span>
              )}{' '}
              {entity.lifecycle !== 'active' && <LifecycleBadge retired />}
            </li>
          ))}
        </ul>
      </section>

      <section className="panel" aria-labelledby="create-heading">
        <h2 id="create-heading">Create entity</h2>
        <ReadOnlyNotice canWrite={canWrite} />
        {canWrite && <EntityForm />}
      </section>
    </>
  )
}
