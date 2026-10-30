/**
 * Asking before a large request (OPS-002). The server refuses a request estimated above
 * the size the author or workspace set with `409 needs_confirmation`, naming the
 * estimate; the client then asks the author here and, if they agree, sends the same
 * request again saying it was confirmed. Nothing was sent or spent by the refusal.
 *
 * The question also says what would be sent and where (CTX-013): the provider, the
 * model, how many words of the story and which pictures. The server asks first whenever
 * pictures would go to a company's servers, whatever the size.
 */

import type { Scope } from '../api/client'

/** What a request would send and where, as the server works it out (CTX-013). */
export interface Sending {
  provider: string
  provider_label: string
  model: string
  destination: string
  where: 'company' | 'your_computer' | 'local_network'
  where_words: string
  words: number
  estimated_tokens: number
  pictures: { asset_id: string; version_id: string; name: string }[]
  context_tokens?: number | null
  fits?: boolean | null
  /** What the author is asked about (the route and the pictures), sent back with the answer. */
  digest?: string
}

export interface SpendQuestion {
  /** The server's own sentence: what this is and about how many tokens it will use. */
  detail: string
  estimatedTokens: number
  thresholdTokens: number | null
  /** What would be sent and where, when the server says (pictures, provider, words). */
  sending?: Sending | null
  /** The story the request is about, for showing the pictures it would send. */
  scope?: Scope
}

type Asker = (question: SpendQuestion) => Promise<boolean>

let asker: Asker | null = null

/** The page that can ask (the dialog host) registers itself; null when it goes. */
export function setSpendAsker(next: Asker | null): void {
  asker = next
}

/** Whether the author agreed. With no page able to ask, nothing is sent. */
export function askToSpend(question: SpendQuestion): Promise<boolean> {
  return asker ? asker(question) : Promise.resolve(false)
}

export function canAskToSpend(): boolean {
  return asker !== null
}
