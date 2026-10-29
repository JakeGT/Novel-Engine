import { describe, expect, it } from 'vitest'
import { withWords } from './draftStream'
import { parseEvents, readEvents } from './sse'

describe('server-sent events', () => {
  it('splits whole events, skips comments and keeps the unfinished rest', () => {
    const { events, rest } = parseEvents(': keep-alive\n\nevent: text\ndata: {"a":1}\n\nevent: job\ndata: {"b"')
    expect(events).toEqual([{ event: 'text', data: '{"a":1}' }])
    expect(rest).toBe('event: job\ndata: {"b"')
  })

  it('reads line endings of every kind and data over several lines', () => {
    const { events } = parseEvents('event: x\r\ndata: one\r\ndata: two\r\n\r\ndata:plain\n\n')
    expect(events).toEqual([
      { event: 'x', data: 'one\ntwo' },
      { event: 'message', data: 'plain' },
    ])
  })

  it('reads a body that arrives in pieces split anywhere, even inside a character', async () => {
    const bytes = new TextEncoder().encode('event: text\ndata: {"t":"café"}\n\nevent: end\ndata: {}\n\n')
    const body = new ReadableStream<Uint8Array>({
      start(control) {
        for (let index = 0; index < bytes.length; index += 3) control.enqueue(bytes.slice(index, index + 3))
        control.close()
      },
    })
    const seen: string[] = []
    await readEvents(body, (event) => seen.push(`${event.event}:${event.data}`))
    expect(seen).toEqual(['text:{"t":"café"}', 'end:{}'])
  })

  it('reads half a million characters of events in linear time', () => {
    const one = 'event: text\ndata: {"number":1,"offset":0,"text":"' + 'a'.repeat(80) + '"}\n\n'
    const text = one.repeat(Math.ceil(500_000 / one.length))
    const started = performance.now()
    let rest = ''
    let count = 0
    for (let index = 0; index < text.length; index += 1000) {
      const parsed = parseEvents(rest + text.slice(index, index + 1000))
      rest = parsed.rest
      count += parsed.events.length
    }
    expect(count).toBe(Math.ceil(500_000 / one.length))
    expect(performance.now() - started).toBeLessThan(1500)
  })
})

describe('a draft’s words', () => {
  it('adds words at the end, and starts again from an earlier offset', () => {
    const empty = { texts: {}, stored: [], streaming: false }
    const first = withWords(empty, 1, 0, 'The stairs ')
    const more = withWords(first, 1, 11, 'wound up.')
    expect(more.texts[1]).toBe('The stairs wound up.')
    expect(more.streaming).toBe(true)
    expect(withWords(more, 1, 0, 'Again.').texts[1]).toBe('Again.')
  })
})
