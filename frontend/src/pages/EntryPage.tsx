import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type CanonEntity } from '../api/client'
import { ActivityPanel } from '../components/ActivityPanel'
import { ArchiveEntry } from '../components/ArchiveEntry'
import { EntryImpact } from '../components/ChangeImpact'
import { CharacterArcs } from '../components/ArcPath'
import { EntityForm } from '../components/EntityForm'
import { EntryImportance } from '../components/EntryImportance'
import { BeliefsPanel } from '../components/BeliefsPanel'
import { ClaimsPanel } from '../components/ClaimsPanel'
import { TrustPanel } from '../components/TrustPanel'
import { KnowledgePanel } from '../components/KnowledgePanel'
import { useStoryClock, whenText } from '../lib/storytime'
import { FactsPanel } from '../components/FactsPanel'
import { EntryMarks } from '../components/EntryMarks'
import { Icon } from '../components/icons'
import { PicturesPanel, Portrait } from '../components/Pictures'
import {
  Badge,
  Button,
  Dialog,
  ErrorNotice,
  Loading,
  ReadOnlyNotice,
  WriteProblem,
} from '../components/ui'
import { NEEDS_DETAIL_KEY, detailsOf, needsDetail } from '../lib/marks'
import { mainPicture } from '../lib/pictures'
import { rememberEntry } from '../lib/recent'
import { typeLabel, typePlural, valueText, words } from '../lib/words'
import { scopedKey, useCanonWrite, useProject } from '../project'

function Details({ entity, onEdit }: { entity: CanonEntity; onEdit?: () => void }) {
  // Tags and importance are shown with the name, not listed as details.
  const attributes = Object.entries(detailsOf(entity.attributes))
  return (
    <section className="card" aria-labelledby="details-heading">
      <div className="card-head">
        <h2 id="details-heading">Details</h2>
      </div>
      {attributes.length === 0 ? (
        <div className="stack tight">
          <p className="muted">
            No details yet.{onEdit ? ' Lasting traits go here, like a role, an age or a look.' : ''}
          </p>
          {/* The way to add them sits where they are missing. */}
          {onEdit && (
            <div>
              <Button size="sm" icon="plus" onClick={onEdit}>
                Add details
              </Button>
            </div>
          )}
        </div>
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

function Events({ entity }: { entity: CanonEntity }) {
  const { scope } = useProject()
  const clock = useStoryClock(scope)
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
            <span className="muted small">
              {event.when
                ? whenText(event.when, clock.unit, clock.eventName)
                : (event.world_position ?? 'not placed in time')}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Assistant({ entity, bare }: { entity: CanonEntity; bare: boolean }) {
  const { scope, fillIn, godHand } = useProject()
  const marked = needsDetail(entity)
  // The author can say an entry needs nothing more, without going through the questions.
  const settle = useCanonWrite<CanonEntity>((expectedRevision) =>
    api.updateEntity(scope, entity.id, {
      attributes: Object.fromEntries(
        Object.entries(entity.attributes ?? {}).filter(([key]) => key !== NEEDS_DETAIL_KEY),
      ),
      expected_revision: expectedRevision,
    }),
  )
  return (
    <section className={`card ${bare ? '' : 'quiet'}`} aria-labelledby="assistant-heading">
      <div className="card-head">
        <div>
          <h2 id="assistant-heading">
            {marked
              ? `${entity.name} is waiting to be filled in`
              : bare
                ? `${entity.name} is just a name so far`
                : `Tell the assistant more about ${entity.name}`}
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
      <div className="actions">
        <Button variant={bare ? 'primary' : 'default'} icon="spark" onClick={() => fillIn?.(entity)}>
          {bare ? 'Fill it in with the assistant' : 'Ask me about it'}
        </Button>
        {!bare && (
          <Button icon="hand" onClick={() => godHand?.(entity)}>
            Change something
          </Button>
        )}
        {marked && (
          <Button variant="ghost" disabled={settle.pending} onClick={() => void settle.submit()}>
            {settle.pending ? 'Saving…' : 'It needs nothing more'}
          </Button>
        )}
      </div>
      <WriteProblem write={settle} scope={scope} />
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
  // The same query the pictures panel uses, so the header shows the entry's picture.
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', entityId),
    queryFn: () => api.listLinks(scope, { target_id: entityId, include_retired: false }),
  })
  useEffect(() => {
    // Remembered so it can be offered again wherever an entry is being chosen.
    if (entity) rememberEntry(scope.projectId, entity.id)
  }, [entity, scope.projectId])
  // Waiting to be filled in: marked so when it was created, or still only a name.
  const bare =
    Boolean(entity) &&
    (needsDetail(entity as CanonEntity) ||
      (Object.keys(detailsOf(entity?.attributes)).length === 0 &&
        stated.isSuccess &&
        stated.data.items.length === 0))

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
            <Portrait entity={entity} link={mainPicture(links.data?.items ?? [])} size="lg" />
            <div className="titles">
              <h1>{entity.name}</h1>
              <div className="actions" style={{ marginTop: '0.4rem' }}>
                <Badge tone="accent">{typeLabel(entity.type)}</Badge>
                {!active && <Badge tone="danger">Archived</Badge>}
                {(entity.aliases ?? []).length > 0 && (
                  <span className="muted">also known as {(entity.aliases ?? []).join(', ')}</span>
                )}
              </div>
              <div style={{ marginTop: '0.5rem' }}>
                <EntryMarks entity={entity} limit={6} />
              </div>
              {editable && <EntryImportance entity={entity} />}
            </div>
            {editable && (
              <div className="actions">
                <Button icon="edit" onClick={() => setEditing(true)}>
                  Edit entry
                </Button>
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
              {entity.type === 'Character' && <BeliefsPanel entity={entity} />}
              <ClaimsPanel entity={entity} />
              {entity.type === 'Character' && <KnowledgePanel entity={entity} />}
              {entity.type === 'Character' && <TrustPanel entity={entity} />}
              {entity.type === 'Character' && <CharacterArcs entity={entity} />}
              {entity.type === 'Character' && <Events entity={entity} />}
              {editable && !bare && <Assistant entity={entity} bare={false} />}
            </div>
            <div className="section-gap">
              <PicturesPanel entity={entity} />
              <ActivityPanel recordType="entity" recordId={entity.id} />
            </div>
          </div>

          {/* Kept well away from Edit: an entry is archived deliberately, not by a slip. */}
          {editable && (
            <section className="danger-zone" aria-labelledby="archive-heading">
              <div>
                <h2 id="archive-heading">Archive</h2>
                <p className="muted small">
                  Takes {entity.name} out of your active story. Its history stays, and you can find
                  it again by showing archived entries.
                </p>
              </div>
              <ArchiveEntry entity={entity} />
            </section>
          )}

          {editing && (
            <Dialog title={`Edit ${entity.name}`} onClose={() => setEditing(false)} wide>
              <div className="stack">
                {/* Before saving: what in the story depends on this entry (CON-017). */}
                <EntryImpact entityId={entity.id} name={entity.name} />
                <EntityForm entity={entity} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />
              </div>
            </Dialog>
          )}
        </>
      )}
    </>
  )
}
