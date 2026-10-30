/**
 * Reading server-sent events from a fetch body (the WHATWG HTML standard, 9.2). Each
 * event is a block of `field: value` lines ended by a blank line; lines starting with
 * `:` are comments (keep-alives) and are skipped. The text is scanned once, so a long
 * stream costs time in proportion to its length.
 */

export interface StreamEvent {
  event: string
  data: string
}

/** Splits what has arrived into whole events; returns them and the unfinished rest. */
export function parseEvents(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = []
  const text = buffer.includes('\r') ? buffer.replace(/\r\n?/g, '\n') : buffer
  let start = 0
  for (;;) {
    const end = text.indexOf('\n\n', start)
    if (end < 0) break
    let event = 'message'
    const data: string[] = []
    for (const line of text.slice(start, end).split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon < 0 ? line : line.slice(0, colon)
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length > 0) events.push({ event, data: data.join('\n') })
    start = end + 2
  }
  return { events, rest: text.slice(start) }
}

/** Reads a body to its end (or until aborted), handing each event on as it arrives. */
export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let rest = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      const parsed = parseEvents(rest + decoder.decode(value, { stream: true }))
      rest = parsed.rest
      for (const event of parsed.events) onEvent(event)
    }
    const last = parseEvents(rest + decoder.decode() + '\n\n')
    for (const event of last.events) onEvent(event)
  } finally {
    reader.releaseLock()
  }
}
