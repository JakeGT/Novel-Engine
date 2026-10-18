import { useState } from 'react'
import { api, type CanonEntity, type Telling } from '../api/client'
import { useCanonWrite, useEntries, useProject } from '../project'
import { BeliefProposal } from './BeliefProposal'
import { useStatement } from './StatementPicker'
import { Button, Dialog, WriteProblem } from './ui'

/**
 * One character tells others something. What each hearer would come to believe follows
 * how they take what the teller says (their trust); someone who overhears believes it
 * less firmly. It is suggested for review, never written straight into the story.
 */
export function TellDialog({ speaker, onClose }: { speaker: CanonEntity; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const { picker, statement } = useStatement(speaker, {
    factLabel: 'Something true in the story',
    otherLabel: 'Something else, which may not be true',
  })

  const [tellerId, setTellerId] = useState(speaker.id)
  const [listeners, setListeners] = useState<string[]>([])
  const [overhearers, setOverhearers] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const [told, setTold] = useState<Telling | null>(null)

  const tell = useCanonWrite<Telling>(
    (expectedRevision) => {
      const chosen = statement()
      return api.tell(scope, {
        speaker_id: tellerId,
        listener_ids: listeners,
        overheard_by: overhearers,
        expected_revision: expectedRevision,
        ...(chosen.fact_id ? { fact_id: chosen.fact_id } : { proposition: chosen.proposition }),
      })
    },
    (result) => setTold(result),
  )

  const toggle = (list: string[], set: (next: string[]) => void, id: string) =>
    set(list.includes(id) ? list.filter((item) => item !== id) : [...list, id])

  const submit = () => {
    if (listeners.length === 0) return setProblem('Choose who is told.')
    const chosen = statement()
    if (chosen.error) return setProblem(chosen.error)
    setProblem(null)
    void tell.submit()
  }

  const teller = characters.find((item) => item.id === tellerId)?.name ?? speaker.name
  const others = characters.filter((item) => item.id !== tellerId)

  if (told) {
    return (
      <Dialog title="Suggested for review" onClose={onClose}>
        <BeliefProposal telling={told} />
      </Dialog>
    )
  }

  return (
    <Dialog title="Tell someone something" onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          Each person told comes to believe it as far as they trust {teller}; you set that on
          their page. Whether it is true is worked out from your facts. What they would believe
          is suggested for your review first.
        </p>
        <label className="field">
          <span>Who tells</span>
          <select value={tellerId} onChange={(event) => setTellerId(event.target.value)}>
            {characters.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="stack tight">
          <legend>Who is told</legend>
          {others.length === 0 && <p className="muted small">There is nobody else to tell yet.</p>}
          <div className="when-kinds">
            {others.map((item) => (
              <label key={item.id} className="check">
                <input
                  type="checkbox"
                  checked={listeners.includes(item.id)}
                  onChange={() => {
                    toggle(listeners, setListeners, item.id)
                    setOverhearers(overhearers.filter((id) => id !== item.id))
                  }}
                />
                <span>{item.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="stack tight">
          <legend>Who overhears it (optional)</legend>
          <div className="when-kinds">
            {others
              .filter((item) => !listeners.includes(item.id))
              .map((item) => (
                <label key={item.id} className="check">
                  <input
                    type="checkbox"
                    checked={overhearers.includes(item.id)}
                    onChange={() => toggle(overhearers, setOverhearers, item.id)}
                  />
                  <span>{item.name}</span>
                </label>
              ))}
          </div>
        </fieldset>
        <fieldset className="stack tight">
          <legend>What {teller} says</legend>
          {picker}
        </fieldset>
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <WriteProblem write={tell} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={tell.pending} onClick={submit}>
            {tell.pending ? 'Working it out…' : 'Suggest what they believe'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
