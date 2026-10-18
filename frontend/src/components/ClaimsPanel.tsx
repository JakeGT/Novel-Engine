import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type CanonEntity, type Claim } from '../api/client'
import { claimSentence } from '../lib/knowledge'
import type { Tone } from '../lib/words'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { ClaimDialog } from './ClaimDialog'
import { Badge, Button, ErrorNotice, Loading, WriteProblem } from './ui'

const STANDING: Record<string, { label: string; tone: Tone }> = {
  borne_out: { label: 'Your facts agree', tone: 'ok' },
  contradicted: { label: 'Your facts say otherwise', tone: 'danger' },
  unconfirmed: { label: 'Your facts don’t say', tone: 'neutral' },
}

/**
 * What is said about or by an entry: by the narration, characters or a narrator. Said, not
 * necessarily true; kept apart from facts, and made a fact only through review.
 */
export function ClaimsPanel({ entity }: { entity: CanonEntity }) {
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const navigate = useNavigate()
  const claims = useQuery({
    queryKey: scopedKey(scope, 'claims', entity.id),
    queryFn: () => api.listClaims(scope, { entity_id: entity.id }),
  })
  const outline = useQuery({ queryKey: scopedKey(scope, 'plan'), queryFn: () => api.outline(scope) })
  const [recording, setRecording] = useState(false)
  const promote = useCanonWrite<{ mutation_id: string }, Claim>(
    (expectedRevision, claim) => api.promoteClaim(scope, claim.event.id, expectedRevision),
    (result) => navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`),
  )
  const items = claims.data?.items ?? []
  const sceneTitle = (id: string | null | undefined) =>
    id ? (outline.data?.items ?? []).find((item) => item.id === id)?.title : undefined
  const editable = canWrite && entity.lifecycle === 'active'

  return (
    <section className="card" aria-labelledby="claims-heading">
      <div className="card-head">
        <div>
          <h2 id="claims-heading">Said in the story</h2>
          <p className="muted small">
            Said, not necessarily true. What the narration or a character says is kept apart from
            your facts, and nobody believes it just because it was said.
          </p>
        </div>
        {editable && (
          <Button size="sm" icon="plus" onClick={() => setRecording(true)}>
            Record something said
          </Button>
        )}
      </div>
      <ErrorNotice error={claims.error} prefix="Could not load what is said" />
      {claims.isPending && <Loading>Loading…</Loading>}
      {claims.data && items.length === 0 && <p className="muted">Nothing recorded yet.</p>}
      {items.length > 0 && (
        <ul className="rows">
          {items.map((claim) => {
            const standing = STANDING[claim.standing] ?? STANDING.unconfirmed
            const scene = sceneTitle(claim.event.claim?.scene_id)
            const heard = claim.perceiver_ids.map((id) => entries.names.get(id) ?? 'someone')
            const narrated = claim.speaker_role === 'narrator'
            const promotable = editable && claim.standing !== 'borne_out' && claim.event.claim?.modality !== 'denies'
            return (
              <li className="row" key={claim.event.id}>
                <span className="grow">
                  <span className="sentence">{claimSentence(claim, entries.names)}</span>
                  {(narrated || scene || heard.length > 0) && (
                    <span className="muted small" style={{ display: 'block' }}>
                      {[
                        narrated ? 'As narrator, perhaps unreliably' : '',
                        scene ? `In “${scene}”` : '',
                        heard.length > 0 ? `Heard by ${heard.join(', ')}` : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  )}
                </span>
                <span className="knowledge-tag">Said</span>
                <Badge tone={standing.tone}>{standing.label}</Badge>
                {promotable && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={promote.pending}
                    aria-label={`Make it a fact: ${claimSentence(claim, entries.names)}`}
                    onClick={() => void promote.submit(claim)}
                  >
                    Make it a fact
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {editable && items.some((claim) => claim.standing !== 'borne_out') && (
        <p className="hint small muted">“Make it a fact” suggests the fact for your review; nothing changes until you approve it.</p>
      )}
      <WriteProblem write={promote} scope={scope} />
      {recording && <ClaimDialog subject={entity} onClose={() => setRecording(false)} />}
    </section>
  )
}
