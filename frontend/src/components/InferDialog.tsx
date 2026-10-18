import { useState } from 'react'
import { api, type CanonEntity, type CharacterKnowledge } from '../api/client'
import { objectText, predicateLabel } from '../lib/words'
import { useCanonWrite, useEntries, useProject } from '../project'
import { useScenePick } from './ScenePick'
import { useStatement } from './StatementPicker'
import { Button, Dialog, WriteProblem } from './ui'

type Said = { subject_id: string; predicate: string; object: Record<string, unknown> }

/**
 * A character works something out from what they know and believe (KNW-002). The author
 * says what, and from what; it is saved at once, as the author's own change, and their
 * page then says it was worked out and from what. Whether they got it right is worked
 * out from the facts, like any belief.
 */
export function InferDialog({
  thinker,
  knowledge,
  onClose,
}: {
  thinker: CanonEntity
  knowledge: CharacterKnowledge
  onClose: () => void
}) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const { picker, statement } = useStatement(thinker, {
    factLabel: 'Something true in the story',
    otherLabel: 'Something else, which may be wrong',
  })
  const where = useScenePick()
  const [premises, setPremises] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const say = (item: Said) =>
    `${entries.names.get(item.subject_id) ?? 'An entry'} ${predicateLabel(item.predicate)} ${objectText(item.object, entries.names)}`
  // What they can work it out from: what they know, and what they believe that the story
  // does not bear out. Each is named once.
  const options = [
    ...knowledge.knows.map(({ fact }) => ({ id: fact.id, text: say(fact), kind: 'Knows' })),
    ...knowledge.wrong.map(({ belief }) => ({ id: belief.id, text: say(belief), kind: 'Believes' })),
    ...knowledge.unconfirmed.map((belief) => ({ id: belief.id, text: say(belief), kind: 'Believes' })),
  ]

  const write = useCanonWrite(
    (expectedRevision) => {
      const chosen = statement()
      return api.infer(scope, {
        holder_id: thinker.id,
        premises,
        confidence: 1,
        scene_id: where.sceneId,
        expected_revision: expectedRevision,
        ...(chosen.fact_id ? { fact_id: chosen.fact_id } : { proposition: chosen.proposition }),
      })
    },
    () => setDone(true),
  )

  const submit = () => {
    const chosen = statement()
    if (chosen.error) return setProblem(chosen.error)
    if (premises.length === 0) return setProblem(`Choose what ${thinker.name} worked it out from.`)
    setProblem(null)
    void write.submit()
  }

  if (done) {
    return (
      <Dialog title="Saved" onClose={onClose}>
        <div className="stack">
          <p>{thinker.name} now believes it, and their page says what they worked it out from.</p>
          <div className="actions end">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog title={`${thinker.name} works something out`} onClose={onClose} wide>
      <div className="stack">
        <fieldset className="stack tight">
          <legend>What {thinker.name} works out</legend>
          {picker}
        </fieldset>
        <fieldset className="stack tight">
          <legend>From what</legend>
          {options.length === 0 ? (
            <p className="muted small">
              {thinker.name} does not know or believe anything yet. Tell them something first.
            </p>
          ) : (
            <ul className="rows">
              {options.map((option) => (
                <li className="row" key={option.id}>
                  <label className="check grow">
                    <input
                      type="checkbox"
                      checked={premises.includes(option.id)}
                      onChange={() =>
                        setPremises(
                          premises.includes(option.id)
                            ? premises.filter((id) => id !== option.id)
                            : [...premises, option.id],
                        )
                      }
                    />
                    <span>{option.text}</span>
                  </label>
                  <span className="knowledge-tag">{option.kind}</span>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        {where.picker}
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <WriteProblem write={write} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={write.pending || options.length === 0} onClick={submit}>
            {write.pending ? 'Saving…' : 'Save what they worked out'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
