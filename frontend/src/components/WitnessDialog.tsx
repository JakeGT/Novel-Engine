import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type Telling } from '../api/client'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { BeliefProposal } from './BeliefProposal'
import { useStatement } from './StatementPicker'
import { Button, Dialog, WriteProblem } from './ui'

/**
 * Something happens in front of people. Everyone there sees it unless the author says
 * they did not notice; each who sees it would believe it, whatever their trust. Suggested
 * for review, never written straight into the story.
 */
export function WitnessDialog({ witness, onClose }: { witness: CanonEntity; onClose: () => void }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scenes = (outline.data?.items ?? []).filter((item) => item.kind === 'scene')
  const { picker, statement } = useStatement(witness, {
    factLabel: 'Something true in the story',
    otherLabel: 'Something else they take it to show',
  })

  const [name, setName] = useState('')
  const [sceneId, setSceneId] = useState('')
  const [present, setPresent] = useState<string[]>([witness.id])
  const [unnoticed, setUnnoticed] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const [seen, setSeen] = useState<Telling | null>(null)

  const write = useCanonWrite<Telling>(
    (expectedRevision) => {
      const chosen = statement()
      return api.witness(scope, {
        name: name.trim(),
        present_ids: present,
        not_noticed: unnoticed,
        expected_revision: expectedRevision,
        ...(chosen.fact_id ? { fact_id: chosen.fact_id } : { proposition: chosen.proposition }),
      })
    },
    (result) => setSeen(result),
  )

  // Choosing a scene puts everyone in it there; the author can then take people out.
  const chooseScene = (id: string) => {
    setSceneId(id)
    const scene = scenes.find((item) => item.id === id)
    if (!scene) return
    const inScene = scene.refs
      .map((ref) => ref.entity_id)
      .filter((entityId) => characters.some((item) => item.id === entityId))
    const everyone = Array.from(new Set(inScene))
    if (everyone.length > 0) {
      setPresent(everyone)
      setUnnoticed(unnoticed.filter((entityId) => everyone.includes(entityId)))
    }
  }

  const toggle = (id: string) => {
    if (present.includes(id)) {
      setPresent(present.filter((item) => item !== id))
      setUnnoticed(unnoticed.filter((item) => item !== id))
    } else {
      setPresent([...present, id])
    }
  }

  const submit = () => {
    if (!name.trim()) return setProblem('Say what happens.')
    if (present.length === 0) return setProblem('Choose who is there.')
    if (present.every((id) => unnoticed.includes(id))) return setProblem('Someone who is there has to notice it.')
    const chosen = statement()
    if (chosen.error) return setProblem(chosen.error)
    setProblem(null)
    void write.submit()
  }

  if (seen) {
    return (
      <Dialog title="Suggested for review" onClose={onClose}>
        <BeliefProposal telling={seen} />
      </Dialog>
    )
  }

  const there = characters.filter((item) => present.includes(item.id))
  return (
    <Dialog title="Someone sees something happen" onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          Everyone there sees it unless you say they did not notice. Seeing is believing: trust
          is about what people are told, not what they see. What each would believe is suggested
          for your review first.
        </p>
        <label className="field">
          <span>What happens</span>
          <input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
        </label>
        {scenes.length > 0 && (
          <label className="field">
            <span>In which scene (optional)</span>
            <select value={sceneId} onChange={(event) => chooseScene(event.target.value)}>
              <option value="">Not in a particular scene</option>
              {scenes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <fieldset className="stack tight">
          <legend>Who is there</legend>
          <div className="when-kinds">
            {characters.map((item) => (
              <label key={item.id} className="check">
                <input type="checkbox" checked={present.includes(item.id)} onChange={() => toggle(item.id)} />
                <span>{item.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
        {there.length > 0 && (
          <fieldset className="stack tight">
            <legend>Who does not notice (optional)</legend>
            <div className="when-kinds">
              {there.map((item) => (
                <label key={item.id} className="check">
                  <input
                    type="checkbox"
                    checked={unnoticed.includes(item.id)}
                    onChange={() =>
                      setUnnoticed(
                        unnoticed.includes(item.id)
                          ? unnoticed.filter((id) => id !== item.id)
                          : [...unnoticed, item.id],
                      )
                    }
                  />
                  <span>{item.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <fieldset className="stack tight">
          <legend>What it shows them</legend>
          {picker}
        </fieldset>
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <WriteProblem write={write} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={write.pending} onClick={submit}>
            {write.pending ? 'Working it out…' : 'Suggest what they believe'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
