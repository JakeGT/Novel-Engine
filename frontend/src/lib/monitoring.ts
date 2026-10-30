// Error reporting from the browser (Sentry), off unless the server hands the page a
// DSN. The server puts what is needed in a <meta> tag on the page it serves, so the
// same build runs anywhere and nothing is baked in when it is built.
//
// Privacy first, as on the server: an event keeps the stack, the page's route, the
// release and the browser. It never carries what the author typed, clicked or
// fetched: no breadcrumbs, no request data, no query strings, no user details.
import * as Sentry from '@sentry/react'
import type { ErrorEvent, EventHint } from '@sentry/react'

export interface ClientConfig {
  sentryDsn: string
  environment: string
  release: string
}

export const CLIENT_CONFIG_META = 'novel-engine-client'

/** What the server put on the page, or null when error reporting is off. */
export function readClientConfig(doc: Document = document): ClientConfig | null {
  const content = doc.querySelector(`meta[name="${CLIENT_CONFIG_META}"]`)?.getAttribute('content')
  if (!content) return null
  try {
    const found = JSON.parse(content) as Partial<ClientConfig>
    if (typeof found.sentryDsn !== 'string' || !/^https:\/\//.test(found.sentryDsn)) return null
    return {
      sentryDsn: found.sentryDsn,
      environment: typeof found.environment === 'string' ? found.environment : 'production',
      release: typeof found.release === 'string' ? found.release : '',
    }
  } catch {
    return null
  }
}

/**
 * An address with nothing private left in it: no query string or fragment, and no
 * invitation token, which is a secret that sits in the path.
 */
export function safeAddress(address: string): string {
  return address
    .replace(/[?#].*$/, '')
    .replace(/\/invite\/[^/]+/, '/invite/[token]')
}

// The only context worth keeping: what the page was running in.
const KEPT_CONTEXTS = new Set(['browser', 'os', 'device'])

/** Remove everything from an event that could carry an author's words or identity. */
export function scrubEvent<T extends ErrorEvent>(event: T, _hint?: EventHint): T {
  delete event.breadcrumbs
  delete event.user
  delete event.extra
  event.tags = {}
  if (event.request) {
    event.request = { url: event.request.url ? safeAddress(event.request.url) : undefined }
  }
  if (event.transaction) event.transaction = safeAddress(event.transaction)
  if (event.contexts) {
    event.contexts = Object.fromEntries(
      Object.entries(event.contexts).filter(([name]) => KEPT_CONTEXTS.has(name)),
    )
  }
  for (const value of event.exception?.values ?? []) {
    for (const frame of value.stacktrace?.frames ?? []) delete frame.vars
  }
  return event
}

/** Start reporting if the page was given a DSN. Returns whether it is on. */
export function startMonitoring(config: ClientConfig | null = readClientConfig()): boolean {
  if (!config) return false
  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environment,
    release: config.release || undefined,
    // This SDK version collects user details, cookies, headers and request bodies
    // unless told not to. Every category is switched off here, before scrubbing.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      graphQL: { document: false, variables: false },
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
      queues: false,
      stackFrameVariables: false,
    },
    // Errors only: no performance traces, no session replays, no visit counting.
    tracesSampleRate: 0,
    integrations: (defaults) =>
      defaults.filter((integration) => !['Breadcrumbs', 'BrowserSession'].includes(integration.name)),
    // Clicks, console lines and requests are never recorded, so they cannot be sent.
    beforeBreadcrumb: () => null,
    beforeSend: scrubEvent,
  })
  return true
}

/** For React's root: report an error React caught or could not recover from. */
export const reportReactError = Sentry.reactErrorHandler()
