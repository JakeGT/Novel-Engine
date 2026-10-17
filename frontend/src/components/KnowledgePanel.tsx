import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type Fact } from '../api/client'
import { objectText, plural, predicateLabel } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { TellDialog } from './TellDialog'
import { Button, ErrorNotice, Loading, UndoNotice, WriteProblem } from './ui'

type Names = Map<string, string>

function sentence(
  item: { subject_id: string; predicate: string; object: Record<string, unknown> },
  names: Names,
): string {
  return `${names.get(item.subject_id) ?? 'An entry'} ${predicateLabel(item.predicate)} ${objectText(item.object, names)}`
}

/**
 * What one character knows, gets wrong, and has no idea of. Derived each time from
 * their beliefs and from what everyone in the story knows; nothing here is stored.
 */
export function KnowledgePanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const known = useQuery({
    queryKey: scopedKey(scope, 'knowledge', entity.id),
    queryFn: () => api.characterKnowledge(scope, entity.id),
  })
  const [telling, setTelling] = useState(false)
  const [madeSecret, setMadeSecret] = useState<Fact | null>(null)
  // Offered where the problem shows: a character believes something the story says is
  // not so. If that is on purpose, the truth may be a secret kept from them.
  const secret = useCanonWrite<Fact, Fact>(
    (expectedRevision, fact) =>
      api.amendFact(scope, fact.id, { expected_revision: expectedRevision, visibility: 'secret' }),
    (_result, fact) => setMadeSecret(fact),
  )
  const unsecret = useCanonWrite<Fact, Fact>(
    (expectedRevision, fact) =>
      api.amendFact(scope, fact.id, {
        expected_revision: expectedRevision,
        visibility: fact.visibility ?? 'ordinary',
      }),
    () => setMadeSecret(null),
  )

  if (entity.type !== 'Character') return null
  const data = known.data
  const names = entries.names

  return (
    <section className="card" aria-labelledby="knowledge-heading">
      <div className="card-head">
        <div>
          <h2 id="knowledge-heading">What {entity.name} knows</h2>
          <p className="muted small">
            Worked out from what {entity.name} believes and what everyone in the story knows.
            Anything else, {entity.name} is not assumed to know.
          </p>
        </div>
        {canWrite && entity.lifecycle === 'active' && (
          <Button size="sm" icon="chat" onClick={() => setTelling(true)}>
            Tell someone something
          </Button>
        )}
      </div>
      <ErrorNotice error={known.error} prefix="Could not work out what they know" />
      {known.isPending && <Loading>Working it out…</Loading>}
      {data && (
        <div className="stack">
          <div>
            <h3 className="label">Knows</h3>
            {data.knows.length === 0 ? (
              <p className="muted">Nothing yet.</p>
            ) : (
              <ul className="rows">
                {data.knows.map(({ fact, how }) => (
                  <li className="row" key={fact.id}>
                    <span className="grow sentence">{sentence(fact, names)}</span>
                    {how === 'everyone' && <span className="knowledge-tag">Everyone knows</span>}
                    {fact.visibility === 'secret' && <span className="knowledge-tag secret">Secret</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {data.wrong.length > 0 && (
            <div>
              <h3 className="label">Believes, but it is not so</h3>
              <ul className="rows">
                {data.wrong.map(({ belief, fact }) => (
                  <li className="row" key={belief.id}>
                    <span className="grow">
                      <span className="sentence">{sentence(belief, names)}</span>
                      <span className="muted small" style={{ display: 'block' }}>
                        In the story: {sentence(fact, names)}
                        {fact.visibility === 'secret' ? ', which is a secret.' : '.'}
                      </span>
                    </span>
                    {canWrite && fact.visibility !== 'secret' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={secret.pending}
                        onClick={() => void secret.submit(fact)}
                      >
                        Keep the truth a secret
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              {canWrite && data.wrong.some(({ fact }) => fact.visibility !== 'secret') && (
                <p className="hint small muted">
                  If {entity.name} is meant to be wrong, keeping the truth a secret stops anyone
                  being assumed to know it.
                </p>
              )}
            </div>
          )}

          {data.unconfirmed.length > 0 && (
            <div>
              <h3 className="label">Believes, and the story does not say</h3>
              <ul className="rows">
                {data.unconfirmed.map((belief) => (
                  <li className="row" key={belief.id}>
                    <span className="grow sentence">{sentence(belief, names)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.unaware.length > 0 && (
            <div>
              <h3 className="label">Secrets {entity.name} does not know</h3>
              <ul className="rows">
                {data.unaware.map((fact) => (
                  <li className="row" key={fact.id}>
                    <span className="grow sentence">{sentence(fact, names)}</span>
                    <span className="knowledge-tag secret">Secret</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.unrecorded > 0 && (
            <p className="muted small">
              {plural(data.unrecorded, 'other fact')} in the story {data.unrecorded === 1 ? 'is' : 'are'}{' '}
              not recorded as known to {entity.name} either way.
            </p>
          )}
        </div>
      )}
      {madeSecret && (
        <UndoNotice busy={unsecret.pending} onUndo={() => void unsecret.submit(madeSecret)}>
          “{sentence(madeSecret, names)}” is now a secret.
        </UndoNotice>
      )}
      <WriteProblem write={secret} scope={scope} />
      <WriteProblem write={unsecret} scope={scope} />
      {telling && <TellDialog speaker={entity} onClose={() => setTelling(false)} />}
    </section>
  )
}
