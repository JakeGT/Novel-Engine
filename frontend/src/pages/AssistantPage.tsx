import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, isApiError } from '../api/client'
import { Badge, Button, EmptyState, ErrorNotice, Field, Loading } from '../components/ui'
import { timeAgo } from '../lib/time'
import { useProject } from '../project'

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
  const [label, setLabel] = useState('OpenRouter')
  const [secret, setSecret] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const add = useMutation({
    mutationFn: () =>
      api.createCredential(workspaceId as string, { label: label.trim(), key: secret.trim() }),
    onSuccess: async () => {
      setSecret('')
      await queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const revoke = useMutation({
    mutationFn: (credentialId: string) => api.revokeCredential(workspaceId as string, credentialId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!label.trim()) return setProblem('Give the key a name so you can recognise it later.')
    if (secret.trim().length < 8) return setProblem('That key looks too short. Paste the whole key.')
    setProblem(null)
    add.mutate()
  }

  if (!workspaceId) {
    return (
      <EmptyState icon="spark" title="Set up on this computer">
        On a self-hosted copy, the AI assistant is configured by whoever runs the server, not from
        this page.
      </EmptyState>
    )
  }

  const forbidden = isApiError(credentials.error) && credentials.error.status === 403
  const unavailable = isApiError(credentials.error, 'capability_unavailable')
  const items = (credentials.data ?? []).filter((item) => item.status === 'active')

  return (
    <>
      <div className="page-head">
        <div>
          <h1>AI assistant</h1>
          <p className="lede">
            The assistant can interview you about a character and draft suggestions for you to
            review. It is optional, and it runs on your own OpenRouter key, so you stay in control
            of the cost. It never changes your story on its own.
          </p>
        </div>
      </div>

      <div className="section-gap">
        {credentials.isPending && <Loading>Loading…</Loading>}
        {forbidden && (
          <p className="notice plain" role="note">
            Only a workspace owner can connect or remove an AI key.
          </p>
        )}
        {unavailable && (
          <p className="notice warn" role="note">
            Key storage is not switched on for this site, so the assistant cannot be connected yet.
            The person who runs the site needs to enable it.
          </p>
        )}
        {!forbidden && !unavailable && <ErrorNotice error={credentials.error} prefix="Could not load keys" />}

        {credentials.isSuccess && (
          <>
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
                        <strong>{item.label}</strong>{' '}
                        <span className="muted">ending in {item.key_last4}</span>
                        <span className="muted small" style={{ display: 'block' }}>
                          Added {timeAgo(item.created_at)}
                          {item.last_used_at ? ` · last used ${timeAgo(item.last_used_at)}` : ' · not used yet'}
                        </span>
                      </span>
                      <Badge tone="ok">Connected</Badge>
                      <Button
                        size="sm"
                        variant="danger-quiet"
                        disabled={revoke.isPending}
                        aria-label={`Remove key ${item.label}`}
                        onClick={() => revoke.mutate(item.id)}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <ErrorNotice error={revoke.error} prefix="The key was not removed" />
            </section>

            <section className="card" aria-labelledby="add-key-heading">
              <div className="card-head">
                <div>
                  <h2 id="add-key-heading">Connect a key</h2>
                  <p className="muted small">
                    Create a key at openrouter.ai, then paste it here. It is stored encrypted and
                    cannot be read back, only replaced or removed.
                  </p>
                </div>
              </div>
              <form className="stack" onSubmit={onSubmit} noValidate>
                <div className="grid-2">
                  <Field label="Name">
                    {(props) => (
                      <input {...props} value={label} onChange={(event) => setLabel(event.target.value)} />
                    )}
                  </Field>
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
                </div>
                <ErrorNotice error={add.error} prefix="The key was not saved" />
                <div className="actions">
                  <Button variant="primary" type="submit" disabled={add.isPending}>
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
