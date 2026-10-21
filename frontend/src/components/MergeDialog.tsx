import { useMutation, useQuery } from '@tanstack/react-query'
import { useId, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type CanonEntity, type MergePreview } from '../api/client'
import { recentEntries } from '../lib/recent'
import { plural, typeLabel } from '../lib/words'
import { scopedKey, useEntries, useInvalidateProject, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Field, Loading } from './ui'

const SHOWN = 12

/** The words two names share, for offering likely twins first. */
function words(name: string): Set<string> {
  return new Set(
    name
      .toLowerCase()
      .split(/[^\p{L}\p{N}]+/u)
      .filter((word) => word.length > 2),
  )
}

/** What a merge would do, as sentences: what moves, what ends instead and why. */
export function MergePreviewView({ preview }: { preview: MergePreview }) {
  const moved = preview.facts_moved.length + preview.beliefs_moved.length
  const follows = [...preview.events, ...preview.plan_items]
  const details = Object.keys(preview.details).length
  return (
    <div className="stack tight merge-preview">
      <p>
        <strong>
          “{preview.merged_name}” becomes part of “{preview.keep_name}”
        </strong>{' '}
        and is archived. Everything known about it moves across.
      </p>
      <dl className="props small">
        <dt>Name</dt>
        <dd>{preview.name}</dd>
        <dt>Also known as</dt>
        <dd>
          {preview.aliases.length > 0 ? preview.aliases.join(', ') : 'No other names'}
          {preview.names_left_out > 0 &&
            ` (${plural(preview.names_left_out, 'other name')} left out: an entry keeps at most ${preview.aliases.length})`}
        </dd>
        <dt>Details</dt>
        <dd>
          {details > 0
            ? `${plural(details, 'detail')}; where both have one, “${preview.keep_name}”’s stays`
            : 'None'}
        </dd>
      </dl>
      <div>
        <p className="label">{moved > 0 ? `Moves to “${preview.keep_name}”` : 'Nothing else moves'}</p>
        {moved > 0 && (
          <ul className="plain-list">
            {[...preview.facts_moved, ...preview.beliefs_moved].map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}
      </div>
      {preview.facts_ended.length + preview.beliefs_ended.length > 0 && (
        <div>
          <p className="label">Ends instead of moving</p>
          <ul className="plain-list">
            {[...preview.facts_ended, ...preview.beliefs_ended].map((item, index) => (
              <li key={index}>
                {item.sentence} <span className="muted small">— {item.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {follows.length > 0 && (
        <div>
          <p className="label">Will name “{preview.keep_name}” instead</p>
          <ul className="plain-list">
            {follows.map((title, index) => (
              <li key={index}>{title}</li>
            ))}
          </ul>
        </div>
      )}
      {preview.pictures_moved + preview.pictures_ended > 0 && (
        <p className="small">
          {preview.pictures_moved > 0 && `${plural(preview.pictures_moved, 'picture')} move across. `}
          {preview.pictures_ended > 0 &&
            `${plural(preview.pictures_ended, 'picture')} ${preview.pictures_ended === 1 ? 'is' : 'are'} already on “${preview.keep_name}”.`}
        </p>
      )}
      {preview.still_naming.length > 0 && (
        <p className="notice plain small" role="note">
          Still naming “{preview.merged_name}”: {preview.still_naming.join(', ')}. What a character
          said keeps its words.
        </p>
      )}
    </div>
  )
}

/** In review: what a suggested merge would do now, read from the story as it is. */
export function MergeWhat({ payload, open }: { payload: Record<string, unknown>; open: boolean }) {
  const { scope } = useProject()
  const body = {
    entity_id: String(payload.id ?? ''),
    into: String(payload.into ?? ''),
    name: typeof payload.name === 'string' ? payload.name : null,
    drop_facts: Array.isArray(payload.drop_facts) ? payload.drop_facts.map(String) : [],
    drop_beliefs: Array.isArray(payload.drop_beliefs) ? payload.drop_beliefs.map(String) : [],
    suggested: true,
  }
  const preview = useQuery({
    queryKey: scopedKey(scope, 'merge-preview', body),
    queryFn: () => api.mergePreview(scope, body),
    enabled: open && Boolean(body.entity_id && body.into),
  })
  if (!open) return null
  if (preview.isPending) return <Loading>Working out what this merge would move…</Loading>
  if (preview.error) {
    return (
      <p className="notice plain small" role="note">
        What this merge would move cannot be worked out now: one of the two entries may have been
        archived or changed since it was suggested.
      </p>
    )
  }
  return <MergePreviewView preview={preview.data} />
}

/**
 * Merge two entries that are the same: pick the other one, choose which to keep, see what
 * would move, then send it to review. Nothing changes until the author approves it there,
 * and once saved it is undone in one step from History.
 */
export function MergeDialog({ entity, onClose }: { entity: CanonEntity; onClose: () => void }) {
  const { scope, refreshHead, headRevision } = useProject()
  const invalidate = useInvalidateProject(scope)
  const navigate = useNavigate()
  const entries = useEntries(scope)
  const [search, setSearch] = useState('')
  const [otherId, setOtherId] = useState<string | null>(null)
  const [keepThis, setKeepThis] = useState(true)
  const [useOtherName, setUseOtherName] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const listId = useId()

  const candidates = useMemo(() => {
    const mine = words(entity.name)
    const recent = recentEntries(scope.projectId)
    const same = entries.items.filter(
      (item) => item.lifecycle === 'active' && item.type === entity.type && item.id !== entity.id,
    )
    const score = (item: CanonEntity) => {
      const shared = [item.name, ...(item.aliases ?? [])].some((label) =>
        [...words(label)].some((word) => mine.has(word)),
      )
      const index = recent.indexOf(item.id)
      return [shared ? 0 : 1, index === -1 ? 99 : index, item.name.toLowerCase()] as const
    }
    const wanted = search.trim().toLowerCase()
    return same
      .filter(
        (item) =>
          !wanted ||
          [item.name, ...(item.aliases ?? [])].some((label) => label.toLowerCase().includes(wanted)),
      )
      .sort((a, b) => {
        const [x, y] = [score(a), score(b)]
        return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2])
      })
  }, [entries.items, entity, scope.projectId, search])

  const other = otherId ? entries.byId.get(otherId) : undefined
  const keep = keepThis ? entity : other
  const gone = keepThis ? other : entity
  const name = useOtherName && gone ? gone.name : null
  const body = keep && gone ? { entity_id: gone.id, into: keep.id, name, suggested: false } : null

  const preview = useQuery({
    queryKey: scopedKey(scope, 'merge-preview', body, headRevision),
    queryFn: () => api.mergePreview(scope, body as NonNullable<typeof body>),
    enabled: Boolean(body),
  })
  const propose = useMutation({
    mutationFn: async () => {
      const head = await refreshHead().catch(() => headRevision)
      const { entity_id, into, name: kept } = body as NonNullable<typeof body>
      return api.proposeMerge(scope, { entity_id, into, name: kept, expected_revision: head })
    },
    onSuccess: async (result) => {
      await invalidate()
      onClose()
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`)
    },
  })

  const shown = showAll ? candidates : candidates.slice(0, SHOWN)
  return (
    <Dialog title={`Merge “${entity.name}” with another entry`} onClose={onClose} wide>
      <div className="stack">
        <p className="muted">
          When two entries are the same {typeLabel(entity.type).toLowerCase()}, merge them: one is
          kept, the other is archived, and everything known about it moves across. You review the
          merge before it is saved, and Undo in History brings everything back.
        </p>

        <fieldset className="stack tight">
          <legend className="label">Which entry is the same as “{entity.name}”?</legend>
          <Field label={`Find a ${typeLabel(entity.type).toLowerCase()} by name`}>
            {(props) => (
              <input
                {...props}
                data-autofocus
                type="search"
                aria-controls={listId}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            )}
          </Field>
          {entries.loading && <Loading>Loading entries…</Loading>}
          <ErrorNotice error={entries.error} prefix="Could not load the entries" />
          {!entries.loading && candidates.length === 0 && (
            <p className="muted small">
              {search.trim()
                ? `No ${typeLabel(entity.type).toLowerCase()} matches “${search.trim()}”.`
                : `There is no other ${typeLabel(entity.type).toLowerCase()} to merge with. Only entries of the same type can be merged.`}
            </p>
          )}
          <ul className="choice-list" id={listId}>
            {shown.map((item) => (
              <li key={item.id}>
                <label className="check">
                  <input
                    type="radio"
                    name="merge-other"
                    checked={otherId === item.id}
                    onChange={() => setOtherId(item.id)}
                  />
                  <span>
                    {item.name}
                    {(item.aliases ?? []).length > 0 && (
                      <span className="muted small"> — also {(item.aliases ?? []).join(', ')}</span>
                    )}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          {candidates.length > shown.length && (
            <div>
              <Button size="sm" variant="ghost" onClick={() => setShowAll(true)}>
                Show all {candidates.length} {typeLabel(entity.type).toLowerCase()} entries
              </Button>
            </div>
          )}
        </fieldset>

        {other && (
          <fieldset className="stack tight">
            <legend className="label">Which one to keep?</legend>
            <label className="check">
              <input type="radio" name="merge-keep" checked={keepThis} onChange={() => setKeepThis(true)} />
              <span>Keep “{entity.name}” and archive “{other.name}”</span>
            </label>
            <label className="check">
              <input type="radio" name="merge-keep" checked={!keepThis} onChange={() => setKeepThis(false)} />
              <span>Keep “{other.name}” and archive “{entity.name}”</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={useOtherName}
                onChange={(event) => setUseOtherName(event.target.checked)}
              />
              <span>Call the kept entry “{gone?.name}” instead of “{keep?.name}”</span>
            </label>
          </fieldset>
        )}

        {body && preview.isPending && <Loading>Working out what would move…</Loading>}
        <ErrorNotice error={preview.error} prefix="Could not work out the merge" />
        {preview.data && (
          <section className="card quiet" aria-label="What the merge would do">
            <MergePreviewView preview={preview.data} />
          </section>
        )}

        <ErrorNotice error={propose.error} prefix="Could not send the merge to review" />
        <div className="actions end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="check"
            disabled={!preview.data || propose.isPending}
            onClick={() => propose.mutate()}
          >
            {propose.isPending ? 'Sending…' : 'Send merge to review'}
          </Button>
        </div>
        {!other && <p className="hint">Choose the other entry to see what the merge would do.</p>}
      </div>
    </Dialog>
  )
}
