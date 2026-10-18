/**
 * Asking before a large request (OPS-002). The server refuses a request estimated above
 * the size the author or workspace set with `409 needs_confirmation`, naming the
 * estimate; the client then asks the author here and, if they agree, sends the same
 * request again saying it was confirmed. Nothing was sent or spent by the refusal.
 */

export interface SpendQuestion {
  /** The server's own sentence: what this is and about how many tokens it will use. */
  detail: string
  estimatedTokens: number
  thresholdTokens: number | null
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
