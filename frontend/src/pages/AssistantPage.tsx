import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, isApiError, type AiChoice, type AiProvider } from '../api/client'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorNotice, Field, Loading } from '../components/ui'
import { timeAgo } from '../lib/time'
import { useProject } from '../project'

/** "openrouter.ai/keys", for showing where a key comes from without the scheme. */
function shortAddress(url: string): string {
  return url.replace(/^https:\/\//, '').replace(/\/$/, '')
}

/**
 * Which model the assistant uses. The list is read from the provider with the
 * workspace's own key, so it is always the current one; a name can also be typed.
 */
function ModelInUse({
  workspaceId,
  providers,
  connected,
  choice,
}: {
  workspaceId: string
  providers: AiProvider[]
  /** Ids of the providers that have a key connected. */
  connected: string[]
  choice: AiChoice
}) {
  const queryClient = useQueryClient()
  const [provider, setProvider] = useState(choice.provider ?? connected[0])
  const [model, setModel] = useState(choice.model ?? '')
  const [problem, setProblem] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const label = (id: string | null | undefined) =>
    providers.find((item) => item.id === id)?.label ?? id ?? ''

  const models = useQuery({
    queryKey: ['ai-models', workspaceId, provider],
    queryFn: () => api.aiModels(workspaceId, provider),
    // A provider's list does not change from minute to minute.
    staleTime: 5 * 60_000,
  })
  const save = useMutation({
    mutationFn: () => api.chooseAi(workspaceId, { provider, model: model.trim() }),
    onSuccess: async (saved) => {
      setSaid(`The assistant now uses ${saved.model} from ${label(saved.provider)}.`)
      await queryClient.invalidateQueries({ queryKey: ['ai-settings', workspaceId] })
    },
  })

  const onProvider = (next: string) => {
    setProvider(next)
    setSaid(null)
    // Start from that provider's suggestion; a model name from another provider is no use.
    setModel(
      next === choice.provider && choice.model
        ? choice.model
        : (providers.find((item) => item.id === next)?.default_model ?? ''),
    )
  }
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setSaid(null)
    if (!model.trim()) return setProblem('Choose a model, or type its name.')
    setProblem(null)
    save.mutate()
  }
  const names = models.data?.models ?? []
  const unchanged = provider === choice.provider && model.trim() === (choice.model ?? '') && choice.chosen

  return (
    <section className="card" aria-labelledby="model-heading">
      <div className="card-head">
        <div>
          <h2 id="model-heading">Model in use</h2>
          {choice.model ? (
            <p className="muted">
              The assistant is using <strong>{choice.model}</strong> from{' '}
              <strong>{label(choice.provider)}</strong>.
              {!choice.chosen && ' This is the suggested model; you can pick another.'}
            </p>
          ) : (
            <p className="notice warn" role="note">
              <strong>Choose a model to switch the assistant on.</strong> A key from{' '}
              {label(choice.provider)} is connected, but no model has been picked for it.
            </p>
          )}
        </div>
      </div>
      <form className="stack" onSubmit={onSubmit} noValidate>
        <div className="grid-2">
          <Field label="Provider" hint="Only providers with a key connected are listed.">
            {(props) => (
              <select {...props} value={provider} onChange={(event) => onProvider(event.target.value)}>
                {connected.map((id) => (
                  <option key={id} value={id}>
                    {label(id)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field
            label="Model"
            error={problem}
            hint={
              models.isPending
                ? 'Asking the provider which models your key can use…'
                : names.length > 0
                  ? `Pick one of the ${names.length} models your key can use, or type a name.`
                  : 'This provider did not give a list. Type the model name exactly as the provider writes it.'
            }
          >
            {(props) => (
              <input
                {...props}
                list="ai-model-names"
                autoComplete="off"
                spellCheck={false}
                value={model}
                onChange={(event) => {
                  setModel(event.target.value)
                  setSaid(null)
                }}
              />
            )}
          </Field>
          <datalist id="ai-model-names">
            {names.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
        <ErrorNotice error={save.error} prefix="The model was not changed" />
        {said && (
          <p className="notice ok" role="status">
            {said}
          </p>
        )}
        <div className="actions">
          <Button variant="primary" type="submit" disabled={save.isPending || unchanged}>
            {save.isPending ? 'Saving…' : 'Use this model'}
          </Button>
          {unchanged && <span className="hint">This is the model in use.</span>}
        </div>
      </form>
    </section>
  )
}

/**
 * The AI assistant runs on the workspace's own provider key. The key is stored
 * encrypted on the server and is never shown again after it is saved.
 */
export function AssistantPage() {
  const { scope } = useProject()
  const workspaceId = scope.workspaceId
  const queryClient = useQueryClient()
  const key = ['credentials', workspaceId]
  const credentials = useQuery({
    queryKey: key,
    queryFn: () => api.listCredentials(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
  const providers = useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => api.aiProviders(),
    enabled: Boolean(workspaceId),
    staleTime: Infinity,
  })
  const choice = useQuery({
    queryKey: ['ai-settings', workspaceId],
    queryFn: () => api.aiSettings(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
  const catalogue = providers.data ?? []
  const [provider, setProvider] = useState('openrouter')
  const [label, setLabel] = useState('')
  const [secret, setSecret] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const picked = catalogue.find((item) => item.id === provider)
  const providerLabel = (id: string) => catalogue.find((item) => item.id === id)?.label ?? id

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: key }),
      queryClient.invalidateQueries({ queryKey: ['ai-settings', workspaceId] }),
    ])
  const add = useMutation({
    mutationFn: () =>
      api.createCredential(workspaceId as string, {
        provider,
        label: label.trim() || (picked?.label ?? provider),
        key: secret.trim(),
      }),
    onSuccess: async () => {
      setSecret('')
      setLabel('')
      await refresh()
    },
  })
  const [removing, setRemoving] = useState<{ id: string; label: string } | null>(null)
  const revoke = useMutation({
    mutationFn: (credentialId: string) => api.revokeCredential(workspaceId as string, credentialId),
    onSuccess: refresh,
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (secret.trim().length < 8) return setProblem('That key looks too short. Paste the whole key.')
    setProblem(null)
    add.mutate()
  }

  if (!workspaceId) {
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Assistant</h1>
          </div>
        </div>
        <EmptyState icon="spark" title="Set up on this computer">
          On a self-hosted copy, the AI assistant is configured by whoever runs the server, not
          from this page.
        </EmptyState>
      </>
    )
  }

  const forbidden = isApiError(credentials.error) && credentials.error.status === 403
  const unavailable = isApiError(credentials.error, 'capability_unavailable')
  const items = (credentials.data ?? []).filter((item) => item.status === 'active')
  const connected = [...new Set(items.map((item) => item.provider))]
  const taken = connected.includes(provider)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>AI assistant</h1>
          <p className="lede">
            The assistant asks you about your story and drafts suggestions for you to review. It
            is optional, and it runs on your own key from the AI provider you choose, so you stay
            in control of the cost. It never changes your story on its own.
          </p>
        </div>
      </div>

      <div className="section-gap">
        {credentials.isPending && <Loading>Loading…</Loading>}
        {forbidden && (
          <p className="notice plain" role="note">
            Only a workspace owner can connect a key or change the model.
            {choice.data?.model &&
              ` The assistant is using ${choice.data.model} from ${providerLabel(choice.data.provider ?? '')}.`}
            {choice.isSuccess && !choice.data.provider && ' No key is connected yet, so the assistant is switched off.'}
          </p>
        )}
        {unavailable && (
          <p className="notice warn" role="note">
            Key storage is not switched on for this site, so the assistant cannot be connected yet.
            The person who runs the site needs to enable it.
          </p>
        )}
        {!forbidden && !unavailable && <ErrorNotice error={credentials.error} prefix="Could not load keys" />}
        <ErrorNotice error={providers.error} prefix="Could not load the list of providers" />

        {credentials.isSuccess && providers.isSuccess && (
          <>
            {choice.isSuccess && choice.data.provider && connected.length > 0 && (
              <ModelInUse
                // Start again from what is saved whenever that changes.
                key={`${choice.data.provider}:${choice.data.model}:${connected.join(',')}`}
                workspaceId={workspaceId}
                providers={catalogue}
                connected={connected}
                choice={choice.data}
              />
            )}

            <section className="card" aria-labelledby="keys-heading">
              <div className="card-head">
                <h2 id="keys-heading">Connected keys</h2>
              </div>
              {items.length === 0 ? (
                <p className="muted">No key is connected, so the assistant is switched off.</p>
              ) : (
                <ul className="rows">
                  {items.map((item) => (
                    <li className="row" key={item.id}>
                      <span className="grow">
                        <strong>{providerLabel(item.provider)}</strong>{' '}
                        <span className="muted">
                          {item.label !== providerLabel(item.provider) && `“${item.label}” · `}key ending in{' '}
                          {item.key_last4}
                        </span>
                        <span className="muted small" style={{ display: 'block' }}>
                          Added {timeAgo(item.created_at)}
                          {item.last_used_at ? ` · last used ${timeAgo(item.last_used_at)}` : ' · not used yet'}
                        </span>
                      </span>
                      {choice.data?.provider === item.provider ? (
                        <Badge tone="ok">In use</Badge>
                      ) : (
                        <Badge>Connected, not in use</Badge>
                      )}
                      <Button
                        size="sm"
                        variant="danger-quiet"
                        aria-label={`Remove the ${providerLabel(item.provider)} key`}
                        onClick={() => setRemoving({ id: item.id, label: providerLabel(item.provider) })}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {removing && (
                <ConfirmDialog
                  title={`Remove the ${removing.label} key?`}
                  confirmLabel="Remove key"
                  keepLabel="Keep key"
                  busy={revoke.isPending}
                  error={revoke.error}
                  onCancel={() => {
                    revoke.reset()
                    setRemoving(null)
                  }}
                  onConfirm={() => revoke.mutate(removing.id, { onSuccess: () => setRemoving(null) })}
                >
                  <p>
                    {connected.length > 1
                      ? 'If the assistant is using this key, it moves to another key you have connected.'
                      : 'The assistant stops working in this workspace until a key is connected again.'}{' '}
                    The key itself is erased here and cannot be recovered; you would paste it in
                    again.
                  </p>
                </ConfirmDialog>
              )}
            </section>

            <section className="card" aria-labelledby="add-key-heading">
              <div className="card-head">
                <div>
                  <h2 id="add-key-heading">Connect a key</h2>
                  <p className="muted small">
                    Choose who provides the AI, create a key with them, and paste it here. It is
                    stored encrypted and cannot be read back, only replaced or removed.
                  </p>
                </div>
              </div>
              <form className="stack" onSubmit={onSubmit} noValidate>
                <Field label="Provider" hint={picked?.note || undefined}>
                  {(props) => (
                    <select {...props} value={provider} onChange={(event) => setProvider(event.target.value)}>
                      {catalogue.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                          {connected.includes(item.id) ? ' (connected)' : ''}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
                {picked && (
                  <p className="hint">
                    Get a key at{' '}
                    <a href={picked.key_url} target="_blank" rel="noreferrer">
                      {shortAddress(picked.key_url)}
                    </a>{' '}
                    (opens in a new tab).
                    {taken && ' A key for this provider is already connected; remove it first to replace it.'}
                  </p>
                )}
                <div className="grid-2">
                  <Field label="Key" error={problem}>
                    {(props) => (
                      <input
                        {...props}
                        type="password"
                        autoComplete="off"
                        value={secret}
                        onChange={(event) => setSecret(event.target.value)}
                      />
                    )}
                  </Field>
                  <Field label="A name for it (optional)" hint="Only to help you recognise it later.">
                    {(props) => (
                      <input {...props} value={label} onChange={(event) => setLabel(event.target.value)} />
                    )}
                  </Field>
                </div>
                <ErrorNotice error={add.error} prefix="The key was not saved" />
                <div className="actions">
                  <Button variant="primary" type="submit" disabled={add.isPending || taken}>
                    {add.isPending ? 'Saving…' : 'Connect key'}
                  </Button>
                </div>
              </form>
            </section>
          </>
        )}
      </div>
    </>
  )
}
