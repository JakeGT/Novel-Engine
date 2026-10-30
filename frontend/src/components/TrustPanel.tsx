import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type Stance, type Trust } from '../api/client'
import { STANCES } from '../lib/knowledge'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Button, ErrorNotice, Loading, WriteProblem } from './ui'

const stanceLabel = (stance: Stance) => STANCES.find((item) => item.value === stance)?.label ?? stance

/**
 * How a character takes what others tell them: in general, and from particular people.
 * Used when a telling suggests what each hearer comes to believe; changes nothing anyone
 * already believes.
 */
export function TrustPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const trust = useQuery({
    queryKey: scopedKey(scope, 'trust', entity.id),
    queryFn: () => api.characterTrust(scope, entity.id),
    enabled: entity.type === 'Character',
  })
  // What the author has changed and not saved yet; until then, what is saved.
  const [draft, setDraft] = useState<{ default: Stance; speakers: Record<string, Stance> } | null>(null)
  const [addId, setAddId] = useState('')
  const [addStance, setAddStance] = useState<Stance>('doubts')
  const [saved, setSaved] = useState(false)
  const general = draft?.default ?? trust.data?.default ?? 'believes'
  const speakers = draft?.speakers ?? trust.data?.speakers ?? {}
  const setGeneral = (value: Stance) => setDraft({ default: value, speakers })
  const setSpeakers = (value: Record<string, Stance>) => setDraft({ default: general, speakers: value })

  const save = useCanonWrite<Trust>(
    (expectedRevision) =>
      api.setCharacterTrust(scope, entity.id, { default: general, speakers, expected_revision: expectedRevision }),
    () => {
      setDraft(null)
      setSaved(true)
    },
  )

  if (entity.type !== 'Character') return null
  const editable = canWrite && entity.lifecycle === 'active'
  const others = entries.items.filter(
    (item) => item.lifecycle === 'active' && item.type === 'Character' && item.id !== entity.id,
  )
  const unlisted = others.filter((item) => !(item.id in speakers))
  const changed =
    draft !== null &&
    trust.data !== undefined &&
    (trust.data.default !== general || JSON.stringify(trust.data.speakers) !== JSON.stringify(speakers))
  const listed = Object.entries(speakers).sort(([a], [b]) =>
    (entries.names.get(a) ?? '').localeCompare(entries.names.get(b) ?? ''),
  )

  const change = (next: () => void) => {
    setSaved(false)
    next()
  }

  return (
    <section className="card" aria-labelledby="trust-heading">
      <div className="card-head">
        <div>
          <h2 id="trust-heading">How {entity.name} takes what others say</h2>
          <p className="muted small">
            Used when someone tells {entity.name} something: it decides what {entity.name} is
            suggested to believe. What {entity.name} sees for themselves is always believed.
          </p>
        </div>
      </div>
      <ErrorNotice error={trust.error} prefix="Could not load this" />
      {trust.isPending && <Loading>Loading…</Loading>}
      {trust.data && (
        <div className="stack">
          <label className="field">
            <span>When anyone tells {entity.name} something</span>
            <select
              value={general}
              disabled={!editable}
              onChange={(event) => change(() => setGeneral(event.target.value as Stance))}
            >
              {STANCES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label} ({item.hint})
                </option>
              ))}
            </select>
          </label>

          <div>
            <h3 className="label">From particular people</h3>
            {listed.length === 0 ? (
              <p className="muted small">Nobody in particular. Everyone is taken the same way.</p>
            ) : (
              <ul className="rows">
                {listed.map(([speakerId, stance]) => {
                  const who = entries.names.get(speakerId) ?? 'Someone no longer in the story'
                  return (
                    <li className="row" key={speakerId}>
                      <span className="grow">{who}</span>
                      {editable ? (
                        <>
                          <select
                            style={{ width: 'auto' }}
                            aria-label={`What ${entity.name} makes of what ${who} says`}
                            value={stance}
                            onChange={(event) =>
                              change(() => setSpeakers({ ...speakers, [speakerId]: event.target.value as Stance }))
                            }
                          >
                            {STANCES.map((item) => (
                              <option key={item.value} value={item.value}>
                                {item.label}
                              </option>
                            ))}
                          </select>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Take ${who} like anyone else`}
                            onClick={() =>
                              change(() =>
                                setSpeakers(Object.fromEntries(listed.filter(([id]) => id !== speakerId))),
                              )
                            }
                          >
                            Remove
                          </Button>
                        </>
                      ) : (
                        <span className="knowledge-tag">{stanceLabel(stance)}</span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          {editable && unlisted.length > 0 && (
            <div className="rule-line">
              <select aria-label="Someone in particular" value={addId} onChange={(event) => setAddId(event.target.value)}>
                <option value="">Someone in particular…</option>
                {unlisted.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <select
                aria-label="What they make of it"
                value={addStance}
                onChange={(event) => setAddStance(event.target.value as Stance)}
              >
                {STANCES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={!addId}
                onClick={() =>
                  change(() => {
                    setSpeakers({ ...speakers, [addId]: addStance })
                    setAddId('')
                  })
                }
              >
                Add
              </Button>
            </div>
          )}

          {editable && (
            <div className="actions">
              <Button variant="primary" disabled={!changed || save.pending} onClick={() => void save.submit()}>
                {save.pending ? 'Saving…' : 'Save'}
              </Button>
              <span className="muted small" role="status">
                {saved && !changed ? 'Saved.' : ''}
              </span>
            </div>
          )}
          <WriteProblem write={save} scope={scope} />
        </div>
      )}
    </section>
  )
}
