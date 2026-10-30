import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  errorMessage,
  isApiError,
  type SaveSearchSetupBody,
  type SaveSearchSetupResult,
  type SearchProvider,
  type SearchSetup,
} from '../api/client'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button, ConfirmDialog, ErrorNotice, Field, Loading } from '../components/ui'
import { displayName, useSession, useSignOut } from '../lib/session'
import { timeAgo } from '../lib/time'
import { initial } from '../lib/words'

// The groups a provider can belong to, in the order they are shown.
const PROVIDER_GROUPS = ['Model makers', 'Hosting and routing', 'Your own'] as const

const SIZE_HINT =
  'How many numbers each passage becomes. More can find closer matches but takes more room. At most 2000.'

/** A server address counts as local only for a loopback host, where no key is needed. */
function isLocalAddress(address: string): boolean {
  const trimmed = address.trim()
  if (!trimmed) return false
  try {
    const parsed = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`)
    const host = parsed.hostname.replace(/^\[|\]$/g, '').toLowerCase()
    return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host.endsWith('.localhost')
  } catch {
    return false
  }
}

function sameAddress(a: string, b: string): boolean {
  const norm = (value: string) => value.trim().replace(/\/+$/, '')
  return norm(a) === norm(b)
}

/** Problems the server names that are shown beside their field, not as a general notice. */
function isFieldProblem(error: unknown): boolean {
  if (isApiError(error, 'sign_in_again')) return error.status === 403
  return isApiError(error, 'key_refused') || isApiError(error, 'model_refused') || isApiError(error, 'size_mismatch')
}

/** One sentence on what is in use now, from where the setup is decided. */
function inUseLine(state: SearchSetup): string {
  if (state.source === 'built_in') {
    return `A model on this server (${state.built_in?.model ?? 'a local model'}). Your writing is not sent anywhere for search.`
  }
  if (state.source === 'off') {
    return 'Off. Passages are found by the words they use.'
  }
  const setup = state.setup
  if (setup) {
    const line = `${setup.provider_label}${setup.host ? ` (${setup.host})` : ''}, ${setup.model}, ${setup.dimension} numbers`
    return state.source === 'server'
      ? `${line}. Set in the server’s settings, so it cannot be changed here.`
      : `${line}.`
  }
  return state.source === 'server'
    ? "Set in the server’s settings, so it cannot be changed here."
    : 'Not set up yet.'
}

/** Where a build sends every passage, said the way the rest of the page says it. */
function buildDestination(state: SearchSetup): string {
  if (state.source === 'built_in') return 'a model on this server'
  return state.setup?.provider_label ?? 'the provider'
}

function initialForm(providers: SearchProvider[], current: SearchSetup) {
  const setup = current.setup
  const first = providers[0]
  const providerId =
    setup?.provider && providers.some((item) => item.id === setup.provider) ? setup.provider : (first?.id ?? '')
  const selected = providers.find((item) => item.id === providerId)
  const modelList = selected?.models ?? []
  const savedModel = setup?.model ?? ''
  const modelIsSuggested = modelList.some((item) => item.id === savedModel)
  const modelChoice =
    modelList.length > 0 && (modelIsSuggested || !savedModel)
      ? modelIsSuggested
        ? savedModel
        : modelList[0].id
      : '__custom__'
  const customModel = modelIsSuggested ? '' : savedModel
  const entry = modelList.find((item) => item.id === modelChoice)
  // The size saved with this model is kept; a fresh choice starts at the model's default.
  const savedSize = setup && setup.provider === providerId && setup.model === (entry?.id ?? savedModel)
  const dimension = savedSize
    ? String(setup.dimension)
    : entry
      ? String(entry.default_dimension ?? entry.dimension)
      : String(selected?.dimension_choices?.[0] ?? '')
  return {
    provider: providerId,
    modelChoice,
    customModel,
    dimension,
    baseUrl: setup?.base_url ?? (selected?.needs_url ? (selected.base_url ?? '') : ''),
  }
}

/** The form that chooses where writing goes for search, and the key for it. */
function SetupForm({
  providers,
  current,
  kekConfigured,
  signInNeeded,
}: {
  providers: SearchProvider[]
  current: SearchSetup
  kekConfigured: boolean
  signInNeeded: boolean
}) {
  const queryClient = useQueryClient()
  const [initial] = useState(() => initialForm(providers, current))
  const [provider, setProvider] = useState(initial.provider)
  const [modelChoice, setModelChoice] = useState(initial.modelChoice)
  const [customModel, setCustomModel] = useState(initial.customModel)
  const [dimension, setDimension] = useState(initial.dimension)
  const [baseUrl, setBaseUrl] = useState(initial.baseUrl)
  const [apiKey, setApiKey] = useState('')
  const [errors, setErrors] = useState<{ key?: string; model?: string; dimension?: string }>({})
  const [said, setSaid] = useState<SaveSearchSetupResult | null>(null)
  const [signInAgain, setSignInAgain] = useState(false)
  const [confirmingStop, setConfirmingStop] = useState(false)

  const selectedProvider = providers.find((item) => item.id === provider) ?? providers[0]
  const modelList = selectedProvider?.models ?? []
  const modelEntry = modelList.find((item) => item.id === modelChoice) ?? null
  const dimensionChoices = selectedProvider?.dimension_choices ?? null
  const fixedDimension = modelEntry && !modelEntry.adjustable ? modelEntry.dimension : null

  const storedKey = current.setup?.key
  const keyCanBeKept =
    Boolean(storedKey?.set) &&
    provider === current.setup?.provider &&
    sameAddress(baseUrl, current.setup?.base_url ?? '')
  const addressIsLocal = Boolean(selectedProvider?.needs_url) && isLocalAddress(baseUrl)
  const keyRequired = !keyCanBeKept && !(addressIsLocal && selectedProvider?.key_optional_when_local)
  const needsSignIn = signInNeeded || signInAgain
  const disabled = !kekConfigured || needsSignIn

  const save = useMutation({
    mutationFn: (body: SaveSearchSetupBody) => api.saveSearchSetup(body),
    onSuccess: (result) => {
      queryClient.setQueryData(['site-search-setup'], result.state)
      setApiKey('')
      setErrors({})
      setSignInAgain(false)
      setSaid(result)
    },
    onError: (err) => {
      setSaid(null)
      if (isApiError(err, 'sign_in_again') && err.status === 403) {
        setSignInAgain(true)
        setErrors({})
        return
      }
      const message = errorMessage(err)
      if (isApiError(err, 'key_refused')) setErrors({ key: message })
      else if (isApiError(err, 'model_refused')) setErrors({ model: message })
      else if (isApiError(err, 'size_mismatch')) setErrors({ dimension: message })
    },
  })
  const stop = useMutation({
    mutationFn: () => api.removeSearchSetup(),
    onSuccess: (state) => {
      queryClient.setQueryData(['site-search-setup'], state)
      setConfirmingStop(false)
    },
  })

  const clear = () => {
    setErrors({})
    setSaid(null)
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setSaid(null)
    setSignInAgain(false)
    const next: typeof errors = {}
    const model = modelChoice === '__custom__' ? customModel.trim() : modelChoice
    if (!model) next.model = 'Choose a model, or type its name.'
    const size = fixedDimension ?? Number(dimension)
    if (!Number.isInteger(size) || size < 1 || size > 2000) {
      next.dimension = 'Size must be a whole number between 1 and 2000.'
    }
    if (keyRequired && apiKey.trim() === '') {
      next.key = `Paste a key from ${selectedProvider?.label ?? 'the provider'}.`
    }
    setErrors(next)
    if (Object.keys(next).length > 0) return
    const body: SaveSearchSetupBody = { provider, model, dimension: size }
    if (selectedProvider?.needs_url) body.base_url = baseUrl.trim()
    if (!keyCanBeKept || apiKey.trim() !== '') body.api_key = apiKey.trim()
    save.mutate(body)
  }

  if (!selectedProvider) {
    return <p className="notice warn" role="note">No providers are available to choose from.</p>
  }

  return (
    <>
      <form className="stack" onSubmit={onSubmit} noValidate>
        {needsSignIn && (
          <div className="notice warn" role="alert">
            <span>For your safety, sign in again before changing the site’s key.</span>
            <a className="btn primary" href="/auth/login?fresh=1&return_to=%2Fsite">
              Sign in again
            </a>
          </div>
        )}
        <Field label="Provider">
          {(props) => (
            <select {...props} value={provider} disabled={disabled} onChange={(event) => {
              const nextId = event.target.value
              const nextProvider = providers.find((item) => item.id === nextId)
              setProvider(nextId)
              clear()
              const models = nextProvider?.models ?? []
              if (models.length > 0) {
                setModelChoice(models[0].id)
                setCustomModel('')
                setDimension(String(models[0].default_dimension ?? models[0].dimension))
              } else {
                setModelChoice('__custom__')
                setCustomModel('')
                setDimension(String(nextProvider?.dimension_choices?.[0] ?? ''))
              }
              setBaseUrl(nextProvider?.needs_url ? (nextProvider.base_url ?? '') : '')
            }}>
              {PROVIDER_GROUPS.map((group) => {
                const inGroup = providers.filter((item) => item.group === group)
                if (inGroup.length === 0) return null
                return (
                  <optgroup key={group} label={group}>
                    {inGroup.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                )
              })}
            </select>
          )}
        </Field>

        <p className="hint">
          {selectedProvider.key_url && (
            <span>
              <a href={selectedProvider.key_url} target="_blank" rel="noopener noreferrer">
                Get a key from {selectedProvider.label}
              </a>{' '}
              (opens in a new tab).
            </span>
          )}
          <a href={selectedProvider.docs} target="_blank" rel="noopener noreferrer">
            How it works
          </a>
        </p>

        {modelList.length > 0 && (
          <Field label="Model" error={modelChoice === '__custom__' ? undefined : errors.model}>
            {(props) => (
              <select {...props} value={modelChoice} disabled={disabled} onChange={(event) => {
                const choice = event.target.value
                setModelChoice(choice)
                clear()
                const entry = modelList.find((item) => item.id === choice)
                if (entry) {
                  setCustomModel('')
                  setDimension(String(entry.default_dimension ?? entry.dimension))
                }
              }}>
                {modelList.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id}
                  </option>
                ))}
                <option value="__custom__">Another model…</option>
              </select>
            )}
          </Field>
        )}
        {(modelChoice === '__custom__' || modelList.length === 0) && (
          <Field
            label={modelList.length > 0 ? 'Model name' : 'Model'}
            error={errors.model}
            hint={modelList.length > 0 ? undefined : 'Type the model name exactly as the provider writes it.'}
          >
            {(props) => (
              <input
                {...props}
                value={customModel}
                disabled={disabled}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setCustomModel(event.target.value)
                  clear()
                }}
              />
            )}
          </Field>
        )}

        {fixedDimension !== null ? (
          <div className="field">
            <span className="label">Size</span>
            <span className="muted">{fixedDimension} numbers. This model’s size is fixed.</span>
          </div>
        ) : dimensionChoices && dimensionChoices.length > 0 ? (
          <Field label="Size" hint={SIZE_HINT} error={errors.dimension}>
            {(props) => (
              <select {...props} value={dimension} disabled={disabled} onChange={(event) => {
                setDimension(event.target.value)
                clear()
              }}>
                {dimensionChoices
                  .concat(dimensionChoices.includes(Number(dimension)) ? [] : [Number(dimension)])
                  .filter((value) => Number.isFinite(value))
                  .map((value) => (
                    <option key={value} value={String(value)}>
                      {value} numbers
                    </option>
                  ))}
              </select>
            )}
          </Field>
        ) : (
          <Field label="Size" hint={SIZE_HINT} error={errors.dimension}>
            {(props) => (
              <input
                {...props}
                type="number"
                min={1}
                max={2000}
                inputMode="numeric"
                value={dimension}
                disabled={disabled}
                onChange={(event) => {
                  setDimension(event.target.value)
                  clear()
                }}
              />
            )}
          </Field>
        )}

        {selectedProvider.needs_url && (
          <Field label="Address" hint="Your server’s address, such as http://localhost:11434/v1">
            {(props) => (
              <input
                {...props}
                value={baseUrl}
                disabled={disabled}
                autoComplete="off"
                spellCheck={false}
                placeholder="http://localhost:11434/v1"
                onChange={(event) => {
                  setBaseUrl(event.target.value)
                  clear()
                }}
              />
            )}
          </Field>
        )}

        <Field
          label="Key"
          error={errors.key}
          hint={
            keyCanBeKept && storedKey
              ? `A key ending in ••••${storedKey.hint ?? ''} is saved (${storedKey.updated_at ? timeAgo(storedKey.updated_at) : 'earlier'}). Leave this empty to keep it.`
              : selectedProvider.key_hint
                ? `${selectedProvider.key_hint.replace(/\.$/, '')}.`
                : undefined
          }
        >
          {(props) => (
            <input
              {...props}
              type="password"
              autoComplete="off"
              value={apiKey}
              disabled={disabled}
              onChange={(event) => {
                setApiKey(event.target.value)
                clear()
              }}
            />
          )}
        </Field>

        <ErrorNotice error={save.error && !isFieldProblem(save.error) ? save.error : null} prefix="Not saved" />
        {said && (
          <p className={`notice ${said.check === 'ok' ? 'ok' : 'warn'}`} role="status">
            {said.message}
          </p>
        )}
        <div className="actions">
          <Button variant="primary" type="submit" disabled={disabled || save.isPending}>
            {save.isPending ? 'Saving…' : 'Check and save'}
          </Button>
          <span className="hint">
            {selectedProvider.label} is asked to read one word with this key before anything is kept.
          </span>
        </div>
      </form>

      {current.source === 'page' && current.setup && (
        <div
          className="actions"
          style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--line)' }}
        >
          <Button variant="danger-quiet" disabled={disabled} onClick={() => setConfirmingStop(true)}>
            Stop using a service
          </Button>
        </div>
      )}

      {confirmingStop && current.setup && (
        <ConfirmDialog
          title={`Stop using ${current.setup.provider_label}?`}
          confirmLabel="Stop using the service"
          keepLabel="Keep using it"
          busy={stop.isPending}
          error={stop.error}
          onCancel={() => {
            stop.reset()
            setConfirmingStop(false)
          }}
          onConfirm={() => stop.mutate()}
        >
          <p>
            The {current.setup.provider_label} key is forgotten. Search by meaning goes back to{' '}
            {current.built_in ? `a model on this server (${current.built_in.model})` : 'off'}. Passages
            are found by the words they use until an index is built again.
          </p>
        </ConfirmDialog>
      )}
    </>
  )
}

/** The search index: built, building, or waiting to be built. */
function SearchIndex({
  index,
  destination,
  build,
  cancel,
}: {
  index: SearchSetup['index']
  destination: string
  build: { pending: boolean; start: () => void }
  cancel: { pending: boolean; start: () => void }
}) {
  if (index.building) {
    const progress = index.build
    const done = progress?.done ?? 0
    const total = progress?.total ?? 0
    return (
      <div className="stack tight">
        <p className="muted">
          Building the search index… {done} of {total} passages.
          {progress?.state === 'failed' && ' It stopped part-way; stop it and build again.'}
        </p>
        <progress className="meter" max={total} value={done} aria-label="Search index build progress" />
        <div className="actions">
          <Button variant="danger-quiet" size="sm" disabled={cancel.pending} onClick={cancel.start}>
            Stop
          </Button>
        </div>
      </div>
    )
  }
  if (index.needs_build) {
    return (
      <div className="stack tight">
        <p className="muted">
          Sends every passage to {destination} once. Until it finishes, passages are found by their
          words.
        </p>
        <div className="actions">
          <Button variant="primary" disabled={build.pending} onClick={build.start}>
            {build.pending ? 'Building…' : 'Build the search index'}
          </Button>
        </div>
      </div>
    )
  }
  if (index.searchable) {
    return (
      <p className="notice ok" role="status">
        Passages are found by what they are about.
      </p>
    )
  }
  return null
}

function SearchMeaningSection() {
  const queryClient = useQueryClient()
  const providers = useQuery({
    queryKey: ['site-search-providers'],
    queryFn: () => api.searchProviders(),
    staleTime: Infinity,
  })
  const setup = useQuery({
    queryKey: ['site-search-setup'],
    queryFn: () => api.searchSetup(),
    // While the index is being built, keep an eye on its progress.
    refetchInterval: (query) => (query.state.data?.index.building ? 2000 : false),
  })
  const build = useMutation({
    mutationFn: () => api.buildSearchIndex(),
    onSuccess: (state) => queryClient.setQueryData(['site-search-setup'], state),
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelSearchBuild(),
    onSuccess: (state) => queryClient.setQueryData(['site-search-setup'], state),
  })

  if (providers.isPending || setup.isPending) return <Loading>Opening search by meaning…</Loading>
  if (setup.error) return <ErrorNotice error={setup.error} prefix="Could not open search by meaning" />
  if (providers.error) return <ErrorNotice error={providers.error} prefix="Could not load the list of providers" />
  const data = setup.data
  if (!data) return null
  const catalogue = providers.data ?? []

  return (
    <>
      <section className="card" aria-labelledby="search-meaning-heading">
        <div className="card-head">
          <div>
            <h2 id="search-meaning-heading">Search by meaning</h2>
            <p className="muted">
              Finds passages by what they are about, not only the words in them. To do that, every
              passage of every story on this site is sent to the provider chosen here, under the
              site’s own account. The site pays; authors are told where their writing goes on their
              Assistant page.
            </p>
          </div>
        </div>
        <div className="stack">
          <p className="notice plain" role="note">
            {inUseLine(data)}
          </p>
          <SearchIndex
            index={data.index}
            destination={buildDestination(data)}
            build={{ pending: build.isPending, start: () => build.mutate() }}
            cancel={{ pending: cancel.isPending, start: () => cancel.mutate() }}
          />
        </div>
      </section>

      {data.editable && (
        <section className="card" aria-labelledby="setup-form-heading">
          <div className="card-head">
            <div>
              <h2 id="setup-form-heading">Set up search by meaning</h2>
              <p className="muted small">
                Choose the provider writing is sent to for search, for every story on this site.
              </p>
            </div>
          </div>
          {!data.kek_configured && (
            <p className="notice warn" role="note">
              This server cannot keep keys yet: its administrator needs to set CREDENTIAL_KEK. See the
              hosting guide.
            </p>
          )}
          <SetupForm
            providers={catalogue}
            current={data}
            kekConfigured={data.kek_configured}
            signInNeeded={data.sign_in_needed}
          />
        </section>
      )}
    </>
  )
}

/**
 * The site’s own settings, for its administrators. Search by meaning is the first
 * thing decided here: where every passage of every story goes so it can be found by
 * what it means.
 */
export function SitePage() {
  const session = useSession()
  const { signOut, pending: signingOut } = useSignOut()

  if (session.isPending) {
    return (
      <div className="center-page">
        <Loading>Opening site settings…</Loading>
      </div>
    )
  }
  if (session.isError) {
    return (
      <div className="center-page">
        <main className="stack" style={{ maxWidth: '30rem', justifyItems: 'start' }}>
          <span className="brand">
            <Logo />
            <span className="wordmark">Novel Engine</span>
          </span>
          <h1>Site settings</h1>
          <p className="lede">Only the site’s administrators can change these settings.</p>
          <a className="btn primary" href="/auth/login?return_to=%2Fsite">
            Sign in
          </a>
        </main>
      </div>
    )
  }

  const admin = session.data.platform_admin

  return (
    <div className="library">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="library-top">
        <Link className="brand" to="/" aria-label="Novel Engine, back to your library">
          <Logo />
          <span className="wordmark">Novel Engine</span>
        </Link>
        <div className="actions">
          <Link className="btn ghost sm" to="/">
            Back to your library
          </Link>
          <ThemeToggle />
          <span className="avatar sm round tint-violet" aria-hidden="true">
            {initial(displayName(session.data))}
          </span>
          <span className="small">{displayName(session.data)}</span>
          <Button variant="ghost" size="sm" icon="logout" onClick={() => void signOut()} disabled={signingOut}>
            Sign out
          </Button>
        </div>
      </header>

      <main id="main" className="section-gap">
        <div className="page-head">
          <div>
            <h1>Site settings</h1>
            <p className="lede">These settings apply to every author and story on this site.</p>
          </div>
        </div>
        {admin ? (
          <SearchMeaningSection />
        ) : (
          <p className="notice plain" role="note">
            Only the site’s administrators can change these settings.
          </p>
        )}
      </main>
    </div>
  )
}
