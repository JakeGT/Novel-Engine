import { useQueries, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Belief, type CanonEntity } from '../api/client'
import { ActivityPanel } from '../components/ActivityPanel'
import { ArchiveEntry } from '../components/ArchiveEntry'
import { EntityForm } from '../components/EntityForm'
import { FactsPanel } from '../components/FactsPanel'
import { Icon } from '../components/icons'
import { MediaLinksPanel } from '../components/MediaLinksPanel'
import {
  Badge,
  Button,
  Dialog,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
  TypeAvatar,
} from '../components/ui'
import { objectText, predicateLabel, typeLabel, typePlural, valueText, words, type Tone } from '../lib/words'
import { scopedKey, useEntries, useProject } from '../project'

const AGREEMENT: Record<string, { label: string; tone: Tone }> = {
  agrees: { label: 'True in canon', tone: 'ok' },
  disagrees: { label: 'Contradicts canon', tone: 'danger' },
  unknown: { label: 'Canon doesn’t say', tone: 'neutral' },
}

function Details({ entity, onEdit }: { entity: CanonEntity; onEdit?: () => void }) {
  const attributes = Object.entries(entity.attributes ?? {})
  return (
    <section className="card" aria-labelledby="details-heading">
      <div className="card-head">
        <h2 id="details-heading">Details</h2>
        {onEdit && (
          <Button size="sm" icon="edit" onClick={onEdit}>
            Edit
          </Button>
        )}
      </div>
      {attributes.length === 0 ? (
        <p className="muted">
          No details yet.{onEdit ? ' Use Edit to add lasting traits like a role, an age or a look.' : ''}
        </p>
      ) : (
        <dl className="props">
          {attributes.map(([key, value]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt>{words(key)}</dt>
              <dd>{valueText(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <details className="more" style={{ marginTop: '1rem' }}>
        <summary>Technical details</summary>
        <dl className="props small">
          <dt>Entry ID</dt>
          <dd>
            <code>{entity.id}</code>
          </dd>
          <dt>Short name</dt>
          <dd>{entity.slug ?? '—'}</dd>
          <dt>Added in</dt>
          <dd>Version {entity.created_revision}</dd>
          {entity.retired_revision != null && (
            <>
              <dt>Archived in</dt>
              <dd>Version {entity.retired_revision}</dd>
            </>
          )}
        </dl>
      </details>
    </section>
  )
}

function beliefSentence(belief: Belief, names: Map<string, string>): string {
  const subject = names.get(belief.subject_id) ?? 'an entry'
  const object = objectText(belief.object as Record<string, unknown>, names)
  return `${subject} ${predicateLabel(belief.predicate)} ${object}`
}

function Beliefs({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const beliefs = useQuery({
    queryKey: scopedKey(scope, 'beliefs', entity.id),
    queryFn: () => api.listBeliefs(scope, { holder_entity_id: entity.id, limit: 200 }),
  })
  const items = beliefs.data?.items ?? []
  const agreements = useQueries({
    queries: items.map((belief) => ({
      queryKey: scopedKey(scope, 'truth-agreement', belief.id),
      queryFn: () => api.truthAgreement(scope, belief.id),
    })),
  })
  if (items.length === 0 && !beliefs.isError) return null
  return (
    <section className="card" aria-labelledby="beliefs-heading">
      <div className="card-head">
        <div>
          <h2 id="beliefs-heading">What {entity.name} believes</h2>
          <p className="muted small">
            Each belief is compared with your current facts every time you look, so this is always
            up to date.
          </p>
        </div>
      </div>
      <ErrorNotice error={beliefs.error} prefix="Could not load beliefs" />
      <ul className="rows">
        {items.map((belief, index) => {
          const agreement = agreements[index]?.data?.truth_agreement
          const view = agreement ? AGREEMENT[agreement] : undefined
          return (
            <li className="row" key={belief.id}>
              <span className="grow sentence">{beliefSentence(belief, entries.names)}</span>
              {belief.confidence < 1 && (
                <span className="muted small">{Math.round(belief.confidence * 100)}% sure</span>
              )}
              {view ? <Badge tone={view.tone}>{view.label}</Badge> : <span className="muted small">checking…</span>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Events({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const events = useQuery({
    queryKey: scopedKey(scope, 'events', 'entity', entity.id),
    queryFn: () => api.listEvents(scope, { entity_id: entity.id, limit: 200 }),
  })
  const taking = (events.data?.items ?? []).filter((event) =>
    event.participants.some((participant) => participant.entity_id === entity.id),
  )
  if (taking.length === 0) return null
  return (
    <section className="card" aria-labelledby="events-heading">
      <div className="card-head">
        <h2 id="events-heading">Events {entity.name} took part in</h2>
      </div>
      <ul className="rows">
        {taking.map((event) => (
          <li className="row" key={event.id}>
            <span className="grow">
              <strong>{event.name}</strong>{' '}
              <span className="muted">
                as{' '}
                {event.participants
                  .filter((participant) => participant.entity_id === entity.id)
                  .map((participant) => participant.role)
                  .join(', ')}
              </span>
            </span>
            <Badge>{words(event.kind)}</Badge>
            <span className="muted small">{event.world_position ?? 'date unknown'}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Assistant({ entity, bare }: { entity: CanonEntity; bare: boolean }) {
  const { fillIn } = useProject()
  return (
    <section className={`card ${bare ? '' : 'quiet'}`} aria-labelledby="assistant-heading">
      <div className="card-head">
        <div>
          <h2 id="assistant-heading">
            {bare ? `${entity.name} is just a name so far` : `Tell the assistant more about ${entity.name}`}
          </h2>
          <p className="muted small">
            The assistant asks a few questions, then drafts the details and the links to the rest
            of your story. Nothing is added until you review it.
          </p>
        </div>
        <span className="avatar sm tint-violet" aria-hidden="true">
          <Icon name="chat" size={15} />
        </span>
      </div>
      <Button variant={bare ? 'primary' : 'default'} icon="spark" onClick={() => fillIn?.(entity)}>
        {bare ? 'Fill it in with the assistant' : 'Ask me about it'}
      </Button>
    </section>
  )
}

/** One entry of the story bible: what it is, what is true about it, and how it got that way. */
export function EntryPage() {
  const { scope, canWrite } = useProject()
  const { entityId = '' } = useParams()
  const [editing, setEditing] = useState(false)

  const query = useQuery({
    queryKey: scopedKey(scope, 'entity', entityId),
    queryFn: () => api.getEntity(scope, entityId),
  })
  const entity = query.data
  const active = entity?.lifecycle === 'active'
  const editable = canWrite && Boolean(active)
  // Outgoing facts decide whether the entry is still only a name; the facts panel shares this query.
  const stated = useQuery({
    queryKey: scopedKey(scope, 'facts', 'subject', entityId),
    queryFn: () => api.listFacts(scope, { subject_id: entityId, limit: 200 }),
  })
  const bare =
    Boolean(entity) &&
    Object.keys(entity?.attributes ?? {}).length === 0 &&
    stated.isSuccess &&
    stated.data.items.length === 0

  return (
    <>
      <Link
        className="crumb"
        to={`/projects/${scope.projectId}/bible${entity ? `?type=${encodeURIComponent(entity.type)}` : ''}`}
      >
        <Icon name="arrowLeft" size={16} /> {entity ? typePlural(entity.type) : 'Story bible'}
      </Link>
      {query.isPending && <Loading>Opening entry…</Loading>}
      <ErrorNotice error={query.error} prefix="Could not open this entry" />
      {entity && (
        <>
          <header className="entry-head">
            <TypeAvatar type={entity.type} name={entity.name} size="lg" />
            <div className="titles">
              <h1>{entity.name}</h1>
              <div className="actions" style={{ marginTop: '0.4rem' }}>
                <Badge tone="accent">{typeLabel(entity.type)}</Badge>
                {!active && <Badge tone="danger">Archived</Badge>}
                {(entity.aliases ?? []).length > 0 && (
                  <span className="muted">also known as {(entity.aliases ?? []).join(', ')}</span>
                )}
              </div>
            </div>
            {editable && (
              <div className="actions">
                <Button icon="edit" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <ArchiveEntry entity={entity} />
              </div>
            )}
          </header>

          <div className="stack" style={{ marginBottom: '1.25rem' }} hidden={canWrite && Boolean(active)}>
            <ReadOnlyNotice canWrite={canWrite} />
            {!active && (
              <p className="notice plain" role="note">
                This entry is archived. It can no longer be changed, and its history is kept.
              </p>
            )}
          </div>

          <div className="two-col">
            <div className="section-gap">
              {editable && bare && <Assistant entity={entity} bare />}
              <Details entity={entity} onEdit={editable ? () => setEditing(true) : undefined} />
              <FactsPanel entity={entity} />
              {entity.type === 'Character' && <Beliefs entity={entity} />}
              {entity.type === 'Character' && <Events entity={entity} />}
              {editable && !bare && <Assistant entity={entity} bare={false} />}
            </div>
            <div className="section-gap">
              <MediaLinksPanel targetId={entity.id} />
              <ActivityPanel recordType="entity" recordId={entity.id} />
            </div>
          </div>

          {editing && (
            <Dialog title={`Edit ${entity.name}`} onClose={() => setEditing(false)} wide>
              <EntityForm entity={entity} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />
            </Dialog>
          )}
        </>
      )}
    </>
  )
}
