import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type Telling } from '../api/client'
import { MODALITIES, type Modality } from '../lib/knowledge'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { BeliefProposal } from './BeliefProposal'
import { useStatement } from './StatementPicker'
import { Button, Dialog, WriteProblem } from './ui'

type Recorded = { revision: number; event_id: string; beliefs?: Telling | null }

/**
 * Record something said: by the narration, by a character in a scene, or by a character
 * narrating (perhaps unreliably). It is kept as something said, never as a fact.
 */
export function ClaimDialog({
  subject,
  sceneId,
  onClose,
}: {
  /** Whom the sentence starts with, at first. */
  subject: CanonEntity
  sceneId?: string
  onClose: () => void
}) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const characters = entries.items.filter((item) => item.lifecycle === 'active' && item.type === 'Character')
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const scenes = (outline.data?.items ?? []).filter((item) => item.kind === 'scene')
  const { picker, statement } = useStatement(subject, {
    factLabel: 'Something your facts already say',
    otherLabel: 'Something else, which may not be true',
  })

  const [speakerId, setSpeakerId] = useState(subject.type === 'Character' ? subject.id : '')
  const [narrates, setNarrates] = useState(false)
  const [modality, setModality] = useState<Modality>('states')
  const [hearers, setHearers] = useState<string[]>([])
  const [scene, setScene] = useState(sceneId ?? '')
  const [believe, setBelieve] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [recorded, setRecorded] = useState<Recorded | null>(null)

  const speaker = characters.find((item) => item.id === speakerId)
  const canBelieve = Boolean(speaker) && !narrates && hearers.length > 0 && (modality === 'states' || modality === 'suggests')

  const write = useCanonWrite<Recorded>(
    (expectedRevision) => {
      const chosen = statement()
      return api.recordClaim(scope, {
        proposition: chosen.proposition ?? { subject_id: subject.id, predicate: '', object: {} },
        modality,
        speaker_id: speakerId || null,
        narrates: Boolean(speakerId) && narrates,
        perceiver_ids: hearers.filter((id) => id !== speakerId),
        scene_id: scene || null,
        hearers_believe: canBelieve && believe,
        expected_revision: expectedRevision,
      })
    },
    (result) => setRecorded(result),
  )

  const submit = () => {
    const chosen = statement()
    if (chosen.error) return setProblem(chosen.error)
    setProblem(null)
    void write.submit()
  }

  if (recorded?.beliefs) {
    return (
      <Dialog title="Recorded, and beliefs suggested" onClose={onClose}>
        <div className="stack">
          <p>It is kept as something said, not as a fact.</p>
          <BeliefProposal telling={recorded.beliefs} />
        </div>
      </Dialog>
    )
  }
  if (recorded) {
    return (
      <Dialog title="Recorded" onClose={onClose}>
        <div className="stack">
          <p>
            It is kept as something said, not as a fact, and nobody believes it because it was
            said. You can make it a fact later; that goes to review first.
          </p>
          <div className="actions end">
            <Button variant="primary" data-autofocus onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      </Dialog>
    )
  }

  const who = speaker?.name ?? 'The narration'
  return (
    <Dialog title="Record something said" onClose={onClose} wide>
      <div className="stack">
        <p className="muted small">
          Something the text, a character or a narrator says. It is kept apart from your facts:
          said, not necessarily true.
        </p>
        <label className="field">
          <span>Who says it</span>
          <select
            value={speakerId}
            onChange={(event) => {
              setSpeakerId(event.target.value)
              setHearers(hearers.filter((id) => id !== event.target.value))
            }}
          >
            <option value="">The narration itself</option>
            {characters.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {speaker && (
          <fieldset className="stack tight">
            <legend>How {speaker.name} says it</legend>
            <label className="check">
              <input type="radio" name="voice" checked={!narrates} onChange={() => setNarrates(false)} />
              <span>Speaking, in the story</span>
            </label>
            <label className="check">
              <input type="radio" name="voice" checked={narrates} onChange={() => setNarrates(true)} />
              <span>Narrating it, perhaps unreliably</span>
            </label>
          </fieldset>
        )}
        <label className="field">
          <span>What {who} does</span>
          <select value={modality} onChange={(event) => setModality(event.target.value as Modality)}>
            {MODALITIES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="stack tight">
          <legend>What is said</legend>
          {picker}
        </fieldset>
        <fieldset className="stack tight">
          <legend>Who hears it (optional)</legend>
          <div className="when-kinds">
            {characters
              .filter((item) => item.id !== speakerId)
              .map((item) => (
                <label key={item.id} className="check">
                  <input
                    type="checkbox"
                    checked={hearers.includes(item.id)}
                    onChange={() =>
                      setHearers(hearers.includes(item.id) ? hearers.filter((id) => id !== item.id) : [...hearers, item.id])
                    }
                  />
                  <span>{item.name}</span>
                </label>
              ))}
          </div>
        </fieldset>
        {scenes.length > 0 && (
          <label className="field">
            <span>In which scene (optional)</span>
            <select value={scene} onChange={(event) => setScene(event.target.value)}>
              <option value="">Not in a particular scene</option>
              {scenes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {canBelieve && speaker && (
          <label className="check">
            <input type="checkbox" checked={believe} onChange={(event) => setBelieve(event.target.checked)} />
            <span>
              Also suggest what those who hear it come to believe, by how far they trust {speaker.name}
            </span>
          </label>
        )}
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <WriteProblem write={write} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={write.pending} onClick={submit}>
            {write.pending ? 'Recording…' : 'Record what is said'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
