import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type CanonEntity } from '../api/client'
import { EntityForm } from '../components/EntityForm'
import { MediaLinksPanel } from '../components/MediaLinksPanel'
import { ProvenancePanel } from '../components/ProvenancePanel'
import { RelationshipsPanel } from '../components/RelationshipsPanel'
import { RetireEntity } from '../components/RetireEntity'
import { ErrorNotice, Json, LifecycleBadge, ReadOnlyNotice } from '../components/ui'
import { scopedKey, useProject } from '../project'

export function EntitySummary({ entity }: { entity: CanonEntity }) {
  const { canWrite } = useProject()
  const [editing, setEditing] = useState(false)
  const active = entity.lifecycle === 'active'

  return (
    <section className="panel" aria-labelledby="entity-heading">
      <h2 id="entity-heading">
        {entity.name} <span className="muted">{entity.type}</span>{' '}
        <LifecycleBadge retired={!active} />
      </h2>
      {editing ? (
        <EntityForm entity={entity} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />
      ) : (
        <>
          <dl>
            <dt>ID</dt>
            <dd>
              <code>{entity.id}</code>
            </dd>
            <dt>Slug</dt>
            <dd>{entity.slug ?? <span className="muted">none</span>}</dd>
            <dt>Aliases</dt>
            <dd>{(entity.aliases ?? []).join(', ') || <span className="muted">none</span>}</dd>
            <dt>Created</dt>
            <dd>r{entity.created_revision}</dd>
            {entity.retired_revision != null && (
              <>
                <dt>Retired</dt>
                <dd>r{entity.retired_revision}</dd>
              </>
            )}
            <dt>Schema version</dt>
            <dd>
              <code>{entity.schema_version_id || 'core'}</code>
            </dd>
            <dt>Attributes</dt>
            <dd>
              {Object.keys(entity.attributes ?? {}).length > 0 ? (
                <Json value={entity.attributes} />
              ) : (
                <span className="muted">none</span>
              )}
            </dd>
          </dl>
          <ReadOnlyNotice canWrite={canWrite} />
          {canWrite && active && (
            <div className="actions">
              <button type="button" className="button" onClick={() => setEditing(true)}>
                Edit
              </button>
              <RetireEntity entity={entity} />
            </div>
          )}
          {!active && (
            <p className="muted">Retired entities are read-only; their history is preserved.</p>
          )}
        </>
      )}
    </section>
  )
}

export function EntityDetail() {
  const { scope } = useProject()
  const { entityId = '' } = useParams()

  const entity = useQuery({
    queryKey: scopedKey(scope, 'entity', entityId),
    queryFn: () => api.getEntity(scope, entityId),
  })

  return (
    <>
      <p>
        <Link to={`/projects/${scope.projectId}`}>← Entities</Link>
      </p>
      {entity.isLoading && (
        <p className="muted" role="status">
          Loading entity…
        </p>
      )}
      <ErrorNotice error={entity.error} />
      {entity.data && (
        <>
          <EntitySummary entity={entity.data} />
          <RelationshipsPanel entity={entity.data} />
          <MediaLinksPanel targetId={entity.data.id} />
          <ProvenancePanel recordType="entity" recordId={entity.data.id} />
        </>
      )}
    </>
  )
}
