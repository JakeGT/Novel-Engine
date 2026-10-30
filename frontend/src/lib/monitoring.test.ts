import type { ErrorEvent } from '@sentry/react'
import { describe, expect, it } from 'vitest'
import { readClientConfig, safeAddress, scrubEvent, startMonitoring } from './monitoring'

function page(meta: string): Document {
  return new DOMParser().parseFromString(`<html><head>${meta}</head><body></body></html>`, 'text/html')
}

describe('browser error reporting', () => {
  it('is off unless the server put a usable configuration on the page', () => {
    expect(readClientConfig(page(''))).toBeNull()
    expect(startMonitoring(readClientConfig(page('')))).toBe(false)
    expect(readClientConfig(page('<meta name="novel-engine-client" content="not json" />'))).toBeNull()
    // A DSN that is not an https address is refused rather than trusted.
    const odd = `<meta name="novel-engine-client" content='{"sentryDsn":"javascript:alert(1)"}' />`
    expect(readClientConfig(page(odd))).toBeNull()

    const good = `<meta name="novel-engine-client" content='{"sentryDsn":"https://key@o1.ingest.sentry.io/2","environment":"production","release":"novel-engine@abc1234"}' />`
    expect(readClientConfig(page(good))).toEqual({
      sentryDsn: 'https://key@o1.ingest.sentry.io/2',
      environment: 'production',
      release: 'novel-engine@abc1234',
    })
  })

  it('never sends a query string, a fragment or an invitation token', () => {
    expect(safeAddress('https://site.example/projects/prj_1/bible?type=Character#top')).toBe(
      'https://site.example/projects/prj_1/bible',
    )
    expect(safeAddress('https://site.example/invite/s3cr3t-token')).toBe('https://site.example/invite/[token]')
  })

  it('strips everything that could carry an author’s words or identity', () => {
    const event = {
      type: undefined,
      message: 'boom',
      breadcrumbs: [{ category: 'ui.click', message: 'button[aria-label="Fill in Mara Venn"]' }],
      user: { id: 'usr_1', email: 'jake@example.com', ip_address: '10.0.0.1' },
      extra: { draft: 'Mara is secretly the heir.' },
      tags: { story: 'The Salt Road' },
      transaction: '/invite/s3cr3t-token',
      request: {
        url: 'https://site.example/invite/s3cr3t-token?next=1',
        headers: { Cookie: 'novel_session=abc', 'User-Agent': 'x' },
        data: 'Mara is secretly the heir.',
        query_string: 'q=Mara',
        cookies: { novel_session: 'abc' },
      },
      contexts: { browser: { name: 'Firefox' }, state: { form: 'Mara is secretly the heir.' } },
      exception: {
        values: [{ type: 'TypeError', value: 'x is undefined', stacktrace: { frames: [{ filename: 'a.js', vars: { name: 'Mara' } }] } }],
      },
    } as unknown as ErrorEvent

    const sent = scrubEvent(event)

    const text = JSON.stringify(sent)
    for (const leaked of ['Mara', 'secretly', 'jake@example.com', '10.0.0.1', 'novel_session', 's3cr3t', 'Salt Road', 'usr_1']) {
      expect(text).not.toContain(leaked)
    }
    // What is needed to find the error survives.
    expect(sent.request).toEqual({ url: 'https://site.example/invite/[token]' })
    expect(sent.contexts).toEqual({ browser: { name: 'Firefox' } })
    expect(sent.exception?.values?.[0]).toMatchObject({ type: 'TypeError', value: 'x is undefined' })
  })
})
