import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Fact, type FactVisibility, type WorldTime } from '../api/client'
import { useStoryClock } from '../lib/storytime'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { Button, Dialog, ErrorNotice, WriteProblem } from './ui'
import { WhenField } from './WhenField'

const WHO_KNOWS: { value: FactVisibility; label: string; hint: string }[] = [
  {
    value: 'ordinary',
    label: 'Nothing said',
    hint: 'Known to the characters who believe it. Nothing more is assumed.',
  },
  {
    value: 'common',
    label: 'Everyone in the story knows it',
    hint: 'Every character knows this without being told.',
  },
  {
    value: 'secret',
    label: 'A secret',
    hint:
      'Only the characters who believe it know it. A reader’s guide leaves it out until a scene shows it.',
  },
]

const same = (a: WorldTime | null | undefined, b: WorldTime | null | undefined) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/** When a fact is true in story time, and who in the story may know it. */
export function FactDetailsDialog({
  fact,
  sentence,
  onClose,
}: {
  fact: Fact
  sentence: string
  onClose: () => void
}) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const clock = useStoryClock(scope)
  const known = useQuery({
    queryKey: scopedKey(scope, 'fact-knowledge', fact.id),
    queryFn: () => api.factKnowledge(scope, fact.id),
  })
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  // Some kinds of fact are true without a time, by how the story's setup defines them.
  const timeless =
    vocabulary.data?.predicates.find((item) => item.predicate === fact.predicate)?.temporal_allowed === false
  const [starts, setStarts] = useState<WorldTime | null>(fact.starts ?? null)
  const [ends, setEnds] = useState<WorldTime | null>(fact.ends ?? null)
  const [visibility, setVisibility] = useState<FactVisibility>(fact.visibility ?? 'ordinary')

  const changed = !same(starts, fact.starts) || !same(ends, fact.ends) || visibility !== fact.visibility
  const save = useCanonWrite<Fact>(
    (expectedRevision) =>
      api.amendFact(scope, fact.id, {
        expected_revision: expectedRevision,
        // An unknown time clears one that was set; leaving a field out keeps it.
        starts: same(starts, fact.starts) ? undefined : (starts ?? { kind: 'unknown' }),
        ends: same(ends, fact.ends) ? undefined : (ends ?? { kind: 'unknown' }),
        visibility: visibility === fact.visibility ? undefined : visibility,
      }),
    () => onClose(),
  )

  const holders = (known.data?.holders ?? []).map((id) => entries.names.get(id) ?? 'someone')
  const mistaken = (known.data?.mistaken ?? []).map((id) => entries.names.get(id) ?? 'someone')
  const believe = (people: string[]) => (people.length === 1 ? 'believes' : 'believe')

  return (
    <Dialog title="When it is true, and who knows it" onClose={onClose} wide>
      <div className="stack">
        <p className="sentence">{sentence}</p>

        {timeless ? (
          <p className="muted small">
            This kind of fact is true without a time, so it cannot be given one here.
          </p>
        ) : (
        <div className="stack tight">
          <h3>When it is true</h3>
          <p className="muted small">
            Leave both as “Not said” if it is simply true. Times are counted in {clock.unit}s;
            change that on the Timeline.
          </p>
          <WhenField
            legend="True from"
            value={starts}
            onChange={setStarts}
            unit={clock.unit}
            events={clock.events}
            disabled={!canWrite}
          />
          <WhenField
            legend="No longer true from"
            value={ends}
            onChange={setEnds}
            unit={clock.unit}
            events={clock.events}
            disabled={!canWrite}
          />
        </div>
        )}

        <fieldset className="stack tight" disabled={!canWrite}>
          <legend>
            <h3>Who in the story knows it</h3>
          </legend>
          {WHO_KNOWS.map((item) => (
            <label key={item.value} className="check">
              <input
                type="radio"
                name="visibility"
                checked={visibility === item.value}
                onChange={() => setVisibility(item.value)}
              />
              <span>
                {item.label}
                <span className="hint small muted" style={{ display: 'block' }}>
                  {item.hint}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        <div className="notice plain">
          <ErrorNotice error={known.error} prefix="Could not check who knows it" />
          {known.data && (
            <p>
              {known.data.everyone
                ? 'Everyone in the story knows this.'
                : holders.length > 0
                  ? `Known to ${holders.join(', ')}, who ${believe(holders)} it.`
                  : 'No character is recorded as knowing this.'}
              {mistaken.length > 0 && ` ${mistaken.join(', ')} ${believe(mistaken)} something else.`}
            </p>
          )}
        </div>

        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          {!changed && canWrite && <span className="muted small">Nothing has changed yet.</span>}
          <Button onClick={onClose}>Cancel</Button>
          {canWrite && (
            <Button
              variant="primary"
              disabled={!changed || save.pending}
              onClick={() => void save.submit()}
            >
              {save.pending ? 'Saving…' : 'Save when and who knows'}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}
