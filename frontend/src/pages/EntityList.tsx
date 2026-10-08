import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { ProjectContext } from './ProjectLayout'

// Core entity types come from the governed core schema (backend registry).
const CORE_TYPES = [
  'Character',
  'Location',
  'Object',
  'Faction',
  'Event',
  'Concept',
  'System',
  'Resource',
  'Constraint',
  'Law',
]

export function EntityList() {
  const { scope, headRevision } = useOutletContext<ProjectContext>()
  const queryClient = useQueryClient()
  const [type, setType] = useState('Character')
  const [name, setName] = useState('')
  const [attributes, setAttributes] = useState('{}')
  const [error, setError] = useState<string | null>(null)

  const entities = useQuery({
    queryKey: ['entities', scope.workspaceId, scope.projectId],
    queryFn: () => api.listEntities(scope),
  })

  const create = useMutation({
    mutationFn: () =>
      api.createEntity(scope, {
        type,
        name,
        attributes: JSON.parse(attributes || '{}') as Record<string, unknown>,
        expected_revision: headRevision,
      }),
    onSuccess: () => {
      setName('')
      setAttributes('{}')
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['entities'] })
      queryClient.invalidateQueries({ queryKey: ['projection'] })
    },
    onError: (mutationError: unknown) =>
      setError(mutationError instanceof Error ? mutationError.message : 'Create failed'),
  })

  const items = entities.data?.items ?? []

  return (
    <section className="panel">
      <h2>Entities</h2>
      {entities.isError && (
        <p className="error">Could not load entities: {(entities.error as Error).message}</p>
      )}
      {items.length === 0 && <p className="muted">No entities yet.</p>}
      <ul className="list">
        {items.map((entity) => (
          <li key={entity.id}>
            <Link to={`/projects/${scope.projectId}/entities/${entity.id}`}>{entity.name}</Link>
            <span className="muted"> {entity.type}</span>
            {entity.lifecycle !== 'active' && <span className="badge warn">{entity.lifecycle}</span>}
          </li>
        ))}
      </ul>

      <h3>Create entity</h3>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        <label>
          Type
          <select value={type} onChange={(event) => setType(event.target.value)}>
            {CORE_TYPES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} required />
        </label>
        <label>
          Attributes (JSON)
          <textarea
            value={attributes}
            onChange={(event) => setAttributes(event.target.value)}
            rows={3}
          />
        </label>
        <button className="button" type="submit" disabled={create.isPending || !name}>
          Create
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </section>
  )
}
