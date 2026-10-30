import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, isApiError, type ComputerCode, type ConnectedComputer } from '../api/client'
import { timeAgo } from '../lib/time'
import { Badge, Button, ConfirmDialog, ErrorNotice, Field, Loading } from './ui'

/** Why a computer stopped working, in plain words. */
const STOPPED_BECAUSE: Record<string, string> = {
  owner: 'Disconnected here',
  access_lost: 'Disconnected: the person who connected it left the workspace',
  expired: 'Disconnected: its connector was not run for 30 days',
  reused: 'Disconnected: an old credential for it was used again, so it may have been copied',
}

function models(count: number): string {
  return `${count} model${count === 1 ? '' : 's'}`
}

function ComputerRow({
  workspaceId,
  computer,
  onChanged,
}: {
  workspaceId: string | null
  computer: ConnectedComputer
  onChanged: () => Promise<unknown>
}) {
  const [model, setModel] = useState(computer.models[0]?.name ?? '')
  const [disconnecting, setDisconnecting] = useState(false)
  const [said, setSaid] = useState<string | null>(null)
  const use = useMutation({
    mutationFn: () => api.useComputer(workspaceId, computer.id, model),
    onSuccess: async (chosen) => {
      setSaid(`The assistant now uses ${model} on “${chosen.name}”.`)
      await onChanged()
    },
  })
  const stop = useMutation({
    mutationFn: () => api.stopUsingComputer(workspaceId),
    onSuccess: async () => {
      setSaid(`The assistant no longer uses “${computer.name}”.`)
      await onChanged()
    },
  })
  const disconnect = useMutation({
    mutationFn: () => api.disconnectComputer(workspaceId, computer.id),
    onSuccess: async () => {
      setDisconnecting(false)
      await onChanged()
    },
  })
  const seen = computer.last_seen_at ? `last seen ${timeAgo(computer.last_seen_at)}` : 'not seen yet'
  const context = computer.models.find((item) => item.name === model)?.context_tokens

  return (
    <li className="row computer-row">
      <div className="grow">
        <strong>{computer.name}</strong>{' '}
        {computer.in_use ? (
          <Badge tone="ok">In use</Badge>
        ) : computer.online ? (
          <Badge tone="info">Connected</Badge>
        ) : (
          <Badge tone="warn">Not running</Badge>
        )}
        <span className="muted small computer-meta">
          {computer.server} · {computer.models.length > 0 ? models(computer.models.length) : 'no models listed yet'} ·{' '}
          {seen} · connector {computer.connector_version}
        </span>
        {!computer.online && (
          <p className="hint">
            The connector on this computer is not running. Start it again to use this computer; it
            stays connected.
          </p>
        )}
        {!computer.up_to_date && (
          <p className="notice warn" role="note">
            This connector is too old for this site, so it cannot take requests. Update it on that
            computer, then start it again.
          </p>
        )}
        {computer.models.length > 0 && (
          <div className="computer-use">
            <Field
              label={`Model on “${computer.name}”`}
              hint={
                context
                  ? `It was given room for ${context.toLocaleString()} tokens.`
                  : 'Its server did not say how much it can read at once.'
              }
            >
              {(props) => (
                <select
                  {...props}
                  value={model}
                  onChange={(event) => {
                    setModel(event.target.value)
                    setSaid(null)
                  }}
                >
                  {computer.models.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <div className="actions">
              <Button
                variant="primary"
                disabled={use.isPending || !model}
                onClick={() => {
                  setSaid(null)
                  use.mutate()
                }}
              >
                {use.isPending ? 'Saving…' : computer.in_use ? 'Use this model' : 'Use for the assistant'}
              </Button>
              {computer.in_use && (
                <Button disabled={stop.isPending} onClick={() => stop.mutate()}>
                  {stop.isPending ? 'Stopping…' : 'Stop using this computer'}
                </Button>
              )}
            </div>
          </div>
        )}
        <ErrorNotice error={use.error} prefix="The model was not changed" />
        <ErrorNotice error={stop.error} prefix="Nothing was changed" />
        {said && (
          <p className="notice ok" role="status">
            {said}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant="danger-quiet"
        aria-label={`Disconnect “${computer.name}”`}
        onClick={() => setDisconnecting(true)}
      >
        Disconnect
      </Button>
      {disconnecting && (
        <ConfirmDialog
          title={`Disconnect “${computer.name}”?`}
          confirmLabel="Disconnect computer"
          keepLabel="Keep it connected"
          busy={disconnect.isPending}
          error={disconnect.error}
          onCancel={() => {
            disconnect.reset()
            setDisconnecting(false)
          }}
          onConfirm={() => disconnect.mutate()}
        >
          <p>
            The connector on it stops working at once, and anything waiting for it is not sent.
            {computer.in_use &&
              ' The assistant stops until you choose another model; it does not move to a key on its own.'}{' '}
            To use this computer again, start the connector on it and type the new code it shows.
          </p>
        </ConfirmDialog>
      )}
    </li>
  )
}

/**
 * A model on the author's own computer (SCN-002A). The connector on that computer shows a
 * short code; typing it here, and confirming what it is, connects the computer.
 */
export function MyComputerCard({ workspaceId }: { workspaceId: string | null }) {
  const queryClient = useQueryClient()
  const key = ['computers', workspaceId]
  const [code, setCode] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [pending, setPending] = useState<{ code: string; found: ComputerCode } | null>(null)
  const [connected, setConnected] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const computers = useQuery({
    queryKey: key,
    queryFn: () => api.computers(workspaceId),
    // Whether each one's connector is running changes on its own.
    refetchInterval: (query) =>
      connected && !(query.state.data ?? []).some((item) => item.models.length > 0) ? 2_000 : 15_000,
  })
  const choice = useQuery({
    queryKey: ['ai-settings', workspaceId],
    queryFn: () => api.aiSettings(workspaceId ?? ''),
  })
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: key }),
      queryClient.invalidateQueries({ queryKey: ['ai-settings', workspaceId] }),
    ])
  const release = useMutation({ mutationFn: () => api.stopUsingComputer(workspaceId), onSuccess: refresh })
  const check = useMutation({
    mutationFn: (typed: string) => api.checkComputerCode(workspaceId, typed),
    onSuccess: (found, typed) => setPending({ code: typed, found }),
  })
  const decide = useMutation({
    mutationFn: ({ approve }: { approve: boolean }) =>
      api.decideComputerCode(workspaceId, pending?.code ?? '', approve),
    onSuccess: async (decided, { approve }) => {
      setPending(null)
      setCode('')
      setConnected(approve ? decided.name : null)
      await refresh()
    },
  })

  const command = `python -m connector --site ${window.location.origin}`
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
    } catch {
      // Clipboard access can be refused; the command is on screen to copy by hand.
    }
  }
  const onCheck = (event: FormEvent) => {
    event.preventDefault()
    setConnected(null)
    const cleaned = code.replace(/[\s-]/g, '')
    if (cleaned.length !== 8) return setProblem('The code has eight letters, like BCDF-GHJK.')
    setProblem(null)
    decide.reset()
    check.mutate(code)
  }

  const forbidden = isApiError(computers.error) && computers.error.status === 403
  const list = computers.data ?? []
  const working = list.filter((item) => item.status === 'connected')
  const stopped = list.filter((item) => item.status !== 'connected')
  const inUse = working.find((item) => item.in_use)

  return (
    <section className="card" aria-labelledby="computer-heading">
      <div className="card-head">
        <div>
          <h2 id="computer-heading">Use a model on your own computer</h2>
          <p className="muted">
            If you run a model with Ollama, LM Studio or a llama.cpp server, the assistant can use
            it from here. A small program on your computer, the connector, asks this site for work:
            nothing on your computer is opened to the internet. Your story’s words go only to the
            computer you connect, and no provider charges for them.
          </p>
        </div>
      </div>

      {choice.data?.provider === 'my_computer' && (
        <p className="notice ok" role="status">
          {inUse ? (
            <span>
              The assistant is using <strong>{choice.data.model}</strong> on “{inUse.name}”.
            </span>
          ) : (
            <span>
              The assistant was set to use a computer that is no longer connected, so it is
              switched off rather than sending your story anywhere else.{' '}
              <Button size="sm" disabled={release.isPending} onClick={() => release.mutate()}>
                {release.isPending ? 'Switching…' : 'Stop using a computer'}
              </Button>
            </span>
          )}
        </p>
      )}
      {forbidden ? (
        <p className="notice plain" role="note">
          Only a workspace owner can connect a computer.
        </p>
      ) : (
        <>
          <ol className="computer-steps">
            <li>Start your model server and load a model.</li>
            <li>
              On the same computer, run the connector. It comes with Novel Engine, in the{' '}
              <code>connector</code> folder, and needs only Python 3.11 or later:
              <div className="computer-command">
                <code className="mono">{command}</code>
                <Button size="sm" onClick={() => void copy()}>
                  {copied ? 'Command copied' : 'Copy command'}
                </Button>
              </div>
              <span className="hint">
                Add <code>--model-server</code> with its address if it is not Ollama’s usual one
                (LM Studio: <code>http://127.0.0.1:1234/v1</code>).
              </span>
            </li>
            <li>It shows a code. Type it here.</li>
          </ol>

          <form className="stack" aria-label="Connect a computer" onSubmit={onCheck} noValidate>
            <div className="computer-code">
              <Field label="Code from the connector" error={problem}>
                {(props) => (
                  <input
                    {...props}
                    className="mono"
                    autoComplete="off"
                    autoCapitalize="characters"
                    spellCheck={false}
                    placeholder="BCDF-GHJK"
                    value={code}
                    onChange={(event) => {
                      setCode(event.target.value.toUpperCase())
                      setPending(null)
                    }}
                  />
                )}
              </Field>
              <Button type="submit" disabled={check.isPending}>
                {check.isPending ? 'Checking…' : 'Check code'}
              </Button>
            </div>
            <ErrorNotice error={check.error} prefix="That code cannot be used" />
          </form>

          {pending && (
            <div className="notice plain computer-confirm" role="group" aria-label="Confirm the computer">
              <p>
                <strong>Connect “{pending.found.name}”?</strong>{' '}
                <span className="muted">
                  Connector {pending.found.connector_version}, asked {timeAgo(pending.found.asked_at)}.
                </span>
              </p>
              <p>
                Only connect a code that a connector you started is showing on your own screen. If
                someone sent you this code, turn it down: connecting it would send your story to
                their computer.
              </p>
              <ErrorNotice error={decide.error} prefix="Nothing was connected" />
              <div className="actions">
                <Button
                  variant="primary"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ approve: true })}
                >
                  {decide.isPending ? 'Connecting…' : 'Connect this computer'}
                </Button>
                <Button disabled={decide.isPending} onClick={() => decide.mutate({ approve: false })}>
                  Turn it down
                </Button>
              </div>
            </div>
          )}
          {connected && !working.some((item) => item.name === connected && item.models.length > 0) && (
            <p className="notice ok" role="status">
              Connected “{connected}”. Once its connector lists its models, choose one below to use
              it for the assistant.
            </p>
          )}

          <h3 className="key-list-heading">Your computers</h3>
          {computers.isPending && <Loading>Loading…</Loading>}
          <ErrorNotice error={computers.error} prefix="Could not load the computers" />
          {computers.isSuccess && working.length === 0 && (
            <p className="muted">No computer is connected.</p>
          )}
          {working.length > 0 && (
            <ul className="rows">
              {working.map((computer) => (
                <ComputerRow
                  // Start again from what is saved whenever its models change.
                  key={`${computer.id}:${computer.models.map((item) => item.name).join(',')}`}
                  workspaceId={workspaceId}
                  computer={computer}
                  onChanged={refresh}
                />
              ))}
            </ul>
          )}
          {stopped.length > 0 && (
            <details className="computer-stopped">
              <summary>Disconnected computers ({stopped.length})</summary>
              <ul className="rows">
                {stopped.map((computer) => (
                  <li className="row" key={computer.id}>
                    <span className="grow">
                      <strong>{computer.name}</strong>
                      <span className="muted small computer-meta">
                        {STOPPED_BECAUSE[computer.disconnected_because ?? 'owner'] ?? 'Disconnected'}
                        {computer.disconnected_at && ` ${timeAgo(computer.disconnected_at)}`}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  )
}
