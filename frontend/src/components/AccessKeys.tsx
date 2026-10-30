import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type AccessKey, type CreatedAccessKey, type Project } from '../api/client'
import { clientConfig } from '../lib/accessKeys'
import { fullDate, timeAgo } from '../lib/time'
import { Badge, Button, ConfirmDialog, ErrorNotice, Field, Loading } from './ui'

/** How long a key lasts: three months unless the person says otherwise; never over a year. */
const LIFETIMES: { days: number; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 180, label: '6 months' },
  { days: 365, label: '1 year' },
]

const STOPPED_BECAUSE: Record<string, string> = {
  holder: 'Withdrawn',
  owner: 'Withdrawn by an owner',
  access_lost: 'Stopped: its holder no longer has access',
  account_closed: 'Stopped: its holder’s account is closed',
}

/** What each thing an assistant can ask for is called here (use is counted, never content). */
const TOOL_WORDS: Record<string, string> = {
  list_stories: 'Listed stories',
  search_entries: 'Searched the story bible',
  read_entry: 'Read entries',
  list_plan: 'Read the plan',
  read_scene: 'Read scenes',
  timeline: 'Read the timeline',
  record_history: 'Read history',
  open_reviews: 'Listed suggestions waiting',
  suggest_changes: 'Suggested changes',
  unknown: 'Asked for something that does not exist',
}

function status(key: AccessKey): { label: string; tone: 'ok' | 'warn' | 'neutral' } {
  if (key.status === 'active') return { label: `Works until ${new Date(key.expires_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}`, tone: 'ok' }
  if (key.status === 'expired') return { label: 'Expired', tone: 'warn' }
  return { label: STOPPED_BECAUSE[key.revoked_reason ?? 'holder'] ?? 'Withdrawn', tone: 'neutral' }
}

function KeyUse({ workspaceId, keyId }: { workspaceId: string | null; keyId: string }) {
  const uses = useQuery({
    queryKey: ['access-key-uses', workspaceId, keyId],
    queryFn: () => api.accessKeyUses(workspaceId, keyId),
  })
  if (uses.isPending) return <Loading>Loading…</Loading>
  if (uses.error) return <ErrorNotice error={uses.error} prefix="Could not load its use" />
  const totals = new Map<string, { calls: number; refused: number }>()
  for (const use of uses.data ?? []) {
    const total = totals.get(use.tool) ?? { calls: 0, refused: 0 }
    totals.set(use.tool, { calls: total.calls + use.calls, refused: total.refused + use.refused })
  }
  if (totals.size === 0) return <p className="muted small">Not used in the last 30 days.</p>
  return (
    <table className="key-use">
      <caption className="sr-only">Use in the last 30 days</caption>
      <thead>
        <tr>
          <th scope="col">What it did</th>
          <th scope="col">Times</th>
          <th scope="col">Refused</th>
        </tr>
      </thead>
      <tbody>
        {[...totals.entries()].map(([tool, total]) => (
          <tr key={tool}>
            <td>{TOOL_WORDS[tool] ?? tool}</td>
            <td>{total.calls}</td>
            <td>{total.refused}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * Access keys for other assistants (QRY-003): make one for this story (or every story), see
 * when each was last used, and withdraw it. The key and the settings that hold it are shown
 * once, right after it is made, and never again.
 */
export function AccessKeys({
  workspaceId,
  projectId,
  projectName,
  canWrite,
  hosted,
}: {
  workspaceId: string | null
  projectId: string
  projectName?: string | null
  canWrite: boolean
  hosted: boolean
}) {
  const queryClient = useQueryClient()
  const keysKey = ['access-keys', workspaceId]
  const keys = useQuery({ queryKey: keysKey, queryFn: () => api.listAccessKeys(workspaceId) })
  const projects = useQuery({
    queryKey: ['projects', workspaceId],
    queryFn: () => api.listProjects(workspaceId as string),
    enabled: hosted && Boolean(workspaceId),
  })
  const names = new Map((projects.data ?? []).map((project: Project) => [project.id, project.name]))
  const storyName = projectName ?? names.get(projectId) ?? null

  const [name, setName] = useState('My assistant')
  const [stories, setStories] = useState<'this' | 'all'>('this')
  const [maySuggest, setMaySuggest] = useState(false)
  const [days, setDays] = useState(90)
  const [made, setMade] = useState<CreatedAccessKey | null>(null)
  const [copied, setCopied] = useState<'key' | 'config' | null>(null)
  const [withdrawing, setWithdrawing] = useState<AccessKey | null>(null)
  const [showingUse, setShowingUse] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [tried, setTried] = useState(false)

  const create = useMutation({
    mutationFn: () =>
      api.createAccessKey(workspaceId, {
        name: name.trim(),
        project_id: stories === 'this' ? projectId : null,
        may_suggest: maySuggest,
        expires_in_days: days,
      }),
    onSuccess: async (result) => {
      setMade(result)
      setCopied(null)
      setSaid(null)
      await queryClient.invalidateQueries({ queryKey: keysKey })
    },
  })
  const withdraw = useMutation({
    mutationFn: (key: AccessKey) => api.revokeAccessKey(workspaceId, key.id),
    onSuccess: async (_result, key) => {
      setWithdrawing(null)
      setSaid(`“${key.name}” no longer works.`)
      if (made?.access_key.id === key.id) setMade(null)
      await queryClient.invalidateQueries({ queryKey: keysKey })
    },
  })

  const onCreate = (event: FormEvent) => {
    event.preventDefault()
    setTried(true)
    if (!name.trim()) return
    setMade(null)
    create.mutate()
  }
  const copy = async (what: 'key' | 'config', text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
    } catch {
      // Clipboard access can be refused; the text is on screen to copy by hand.
      setCopied(null)
    }
  }
  const nameMissing = tried && !name.trim()
  const where = (key: AccessKey) => {
    if (key.project_id === null) return 'Every story you can read here'
    if (key.project_id === projectId) return storyName ? `This story (${storyName})` : 'This story'
    return names.get(key.project_id) ?? 'Another story'
  }

  return (
    <section className="card" aria-labelledby="access-keys-heading">
      <div className="card-head">
        <div>
          <h2 id="access-keys-heading">Other assistants</h2>
          <p className="muted small">
            Let an assistant you already use, such as Claude or ChatGPT, read your story through
            an access key. It can look things up and, if you allow it, suggest changes. It never
            changes the story itself: every suggestion waits in Review for you.
          </p>
        </div>
      </div>

      <form className="stack" onSubmit={onCreate} noValidate aria-label="Make an access key">
        <div className="grid-2">
          <Field
            label="Name"
            hint="Say which assistant or computer will use it, so you can tell keys apart."
            error={nameMissing ? 'Give the key a name.' : undefined}
          >
            {(props) => (
              <input
                {...props}
                maxLength={100}
                autoComplete="off"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>
          <Field label="Works for" hint="After this it stops working; you can make a new one.">
            {(props) => (
              <select {...props} value={days} onChange={(event) => setDays(Number(event.target.value))}>
                {LIFETIMES.map((item) => (
                  <option key={item.days} value={item.days}>
                    {item.label}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>
        {hosted && (
          <fieldset className="stack tight">
            <legend>Which stories</legend>
            <label className="check">
              <input type="radio" name="key-stories" checked={stories === 'this'} onChange={() => setStories('this')} />
              <span>{storyName ? `Only this story (${storyName})` : 'Only this story'}</span>
            </label>
            <label className="check">
              <input type="radio" name="key-stories" checked={stories === 'all'} onChange={() => setStories('all')} />
              <span>Every story you can read in this workspace</span>
            </label>
          </fieldset>
        )}
        <fieldset className="stack tight">
          <legend>What it may do</legend>
          <label className="check">
            <input type="radio" name="key-may" checked={!maySuggest} onChange={() => setMaySuggest(false)} />
            <span>Read only</span>
          </label>
          <label className="check">
            <input
              type="radio"
              name="key-may"
              checked={maySuggest}
              disabled={!canWrite}
              onChange={() => setMaySuggest(true)}
            />
            <span>
              Read and suggest changes
              <span className="muted small" style={{ display: 'block' }}>
                {canWrite
                  ? 'Suggestions go to Review. Nothing changes until you keep them.'
                  : 'Only someone who can change the story can allow this.'}
              </span>
            </span>
          </label>
        </fieldset>
        <div className="actions">
          <Button variant="primary" type="submit" disabled={create.isPending}>
            {create.isPending ? 'Making the key…' : 'Make access key'}
          </Button>
        </div>
        <ErrorNotice error={create.error} prefix="The key was not made" />
      </form>

      {made && (
        <div className="notice ok access-key-made" role="status">
          <p>
            <strong>Copy this key now.</strong> It is shown only once and cannot be shown again.
            Anyone who has it can {made.access_key.may_suggest ? 'read and suggest changes to' : 'read'}{' '}
            {made.access_key.project_id ? 'this story' : 'your stories here'}, so keep it like a
            password.
          </p>
          <Field label="Access key">
            {(props) => (
              <input
                {...props}
                className="mono"
                readOnly
                value={made.key}
                onFocus={(event) => event.target.select()}
              />
            )}
          </Field>
          <div className="actions">
            <Button onClick={() => void copy('key', made.key)}>{copied === 'key' ? 'Key copied' : 'Copy key'}</Button>
          </div>
          <p>
            To connect an assistant, add this to its MCP settings (in Claude Desktop, Claude Code,
            Cursor and most others it goes under <code>mcpServers</code>):
          </p>
          <Field label="Settings for your assistant">
            {(props) => (
              <textarea
                {...props}
                className="mono"
                readOnly
                rows={11}
                value={clientConfig(made.key)}
                onFocus={(event) => event.target.select()}
              />
            )}
          </Field>
          <div className="actions">
            <Button onClick={() => void copy('config', clientConfig(made.key))}>
              {copied === 'config' ? 'Settings copied' : 'Copy settings'}
            </Button>
          </div>
        </div>
      )}

      <h3 className="key-list-heading">Your keys{hosted ? ' and others’ you manage' : ''}</h3>
      {keys.isPending && <Loading>Loading…</Loading>}
      <ErrorNotice error={keys.error} prefix="Could not load access keys" />
      {keys.isSuccess && keys.data.length === 0 && <p className="muted">No keys yet.</p>}
      <ul className="rows">
        {(keys.data ?? []).map((key) => {
          const state = status(key)
          const working = key.status === 'active'
          return (
            <li className={`row access-key${working ? '' : ' stopped'}`} key={key.id}>
              <span className="grow">
                <strong>{key.name}</strong>
                {!key.mine && key.holder_name && <span className="muted"> · {key.holder_name}’s</span>}
                <span className="muted small" style={{ display: 'block' }}>
                  {where(key)} · {key.may_suggest ? 'reads and suggests' : 'reads only'} · ends in …
                  {key.hint}
                </span>
                <span className="muted small" style={{ display: 'block' }} title={key.last_used_at ? fullDate(key.last_used_at) : undefined}>
                  {key.last_used_at ? `Last used ${timeAgo(key.last_used_at)}` : 'Not used yet'} · made{' '}
                  {timeAgo(key.created_at)}
                </span>
              </span>
              <Badge tone={state.tone}>{state.label}</Badge>
              <Button
                size="sm"
                variant="ghost"
                aria-expanded={showingUse === key.id}
                aria-label={`${showingUse === key.id ? 'Hide' : 'Show'} use of ${key.name}`}
                onClick={() => setShowingUse(showingUse === key.id ? null : key.id)}
              >
                {showingUse === key.id ? 'Hide use' : 'Show use'}
              </Button>
              {working && (
                <Button
                  size="sm"
                  variant="danger-quiet"
                  aria-label={`Withdraw ${key.name}`}
                  onClick={() => setWithdrawing(key)}
                >
                  Withdraw
                </Button>
              )}
              {showingUse === key.id && (
                <div className="key-use-wrap">
                  <KeyUse workspaceId={workspaceId} keyId={key.id} />
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {said && (
        <p className="notice ok" role="status" style={{ marginTop: '0.75rem' }}>
          {said}
        </p>
      )}
      {withdrawing && (
        <ConfirmDialog
          title={`Withdraw “${withdrawing.name}”?`}
          confirmLabel="Withdraw key"
          keepLabel="Keep it"
          busy={withdraw.isPending}
          error={withdraw.error}
          onCancel={() => {
            withdraw.reset()
            setWithdrawing(null)
          }}
          onConfirm={() => withdraw.mutate(withdrawing)}
        >
          <p>
            Any assistant using it stops reaching your story straight away. This cannot be undone,
            but you can make a new key. Suggestions it already made stay in Review.
          </p>
        </ConfirmDialog>
      )}
    </section>
  )
}
