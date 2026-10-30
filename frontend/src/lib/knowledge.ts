// Words for what people believe, are told and say (KNW-005, KNW-006).
import type { Claim, Stance } from '../api/client'
import { objectText, predicateLabel } from './words'

/** How firmly someone would believe something, in words; the number is what is kept. */
export function firmness(confidence: number): string {
  if (confidence >= 1) return 'Believes it'
  if (confidence >= 0.5) return 'Believes it, less firmly'
  return 'Half believes it'
}

/** How a character takes what others tell them. */
export const STANCES: { value: Stance; label: string; hint: string }[] = [
  { value: 'believes', label: 'Believes it', hint: 'takes it as said' },
  { value: 'doubts', label: 'Doubts it', hint: 'believes it, less firmly' },
  { value: 'disbelieves', label: 'Does not believe it', hint: 'nothing is added' },
]

export type Modality = 'states' | 'suggests' | 'wonders' | 'denies'

/** How something is said. */
export const MODALITIES: { value: Modality; label: string }[] = [
  { value: 'states', label: 'Says it is so' },
  { value: 'suggests', label: 'Suggests it, without saying it' },
  { value: 'wonders', label: 'Wonders whether it is so' },
  { value: 'denies', label: 'Says it is not so' },
]

const SAYS: Record<string, string> = {
  states: 'says',
  suggests: 'suggests',
  wonders: 'wonders whether',
  denies: 'says it is not so that',
}

/** One claim as a sentence: who says it, how, and what. Names are read as they are now. */
export function claimSentence(claim: Claim, names: Map<string, string>): string {
  const said = claim.event.claim
  if (!said) return claim.event.name
  const who = claim.speaker_id ? (names.get(claim.speaker_id) ?? 'Someone') : 'The narration'
  const what = `${names.get(said.subject_id) ?? 'an entry'} ${predicateLabel(said.predicate)} ${objectText(said.object, names)}`
  return `${who} ${SAYS[said.modality] ?? 'says'} ${what}`
}
