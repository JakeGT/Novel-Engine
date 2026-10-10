import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, isApiError, type MemberRole } from '../api/client'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorNotice, Field, Loading } from '../components/ui'
import { fullDate, timeAgo } from '../lib/time'
import { initial, roleLabel } from '../lib/words'
import { useProject } from '../project'

/** "an Owner", "a Reader". */
function article(word: string): string {
  return `${/^[aeiou]/i.test(word) ? 'an' : 'a'} ${word}`
}

const ROLES: { value: MemberRole; label: string; can: string }[] = [
  { value: 'editor', label: 'Editor', can: 'Can add to and change the story' },
  { value: 'viewer', label: 'Reader', can: 'Can read everything, change nothing' },
  { value: 'owner', label: 'Owner', can: 'Can also manage people, keys, and projects' },
]


/** Who can see and change this workspace's stories, and a way to invite more people. */
export function PeoplePage() {
  const { scope, userId, access } = useProject()
  const workspaceId = scope.workspaceId
  const queryClient = useQueryClient()
  const membersKey = ['members', workspaceId]
  const invitationsKey = ['invitations', workspaceId]
  const manage = access?.role === 'owner'

  const members = useQuery({
    queryKey: membersKey,
    queryFn: () => api.listMembers(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
  const invitations = useQuery({
    queryKey: invitationsKey,
    queryFn: () => api.listInvitations(workspaceId as string),
    enabled: Boolean(workspaceId) && manage,
  })
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: membersKey }),
      queryClient.invalidateQueries({ queryKey: invitationsKey }),
    ])

  const [email, setEmail] = useState('')
  const [role, setRole] = useState<MemberRole>('editor')
  const [created, setCreated] = useState<{ link: string; email: string | null } | null>(null)
  const [copied, setCopied] = useState(false)
  // Fixed when the page opens, so the list of unexpired invitations is stable while it is shown.
  const [openedAt] = useState(() => Date.now())

  const invite = useMutation({
    mutationFn: () =>
      api.createInvitation(workspaceId as string, { role, email: email.trim() || null }),
    onSuccess: async (result) => {
      // The token exists only in this answer: show the link now, because it cannot be shown again.
      setCreated({
        link: `${window.location.origin}/invite/${encodeURIComponent(result.token)}`,
        email: result.invitation.email ?? null,
      })
      setCopied(false)
      setEmail('')
      await refresh()
    },
  })
  const revoke = useMutation({
    mutationFn: (invitationId: string) => api.revokeInvitation(workspaceId as string, invitationId),
    onSuccess: refresh,
  })
  // What was just changed, said in words: a changed menu alone is easy to miss.
  const [said, setSaid] = useState<string | null>(null)
  const [removing, setRemoving] = useState<{ userId: string; name: string } | null>(null)
  const setMemberRole = useMutation({
    mutationFn: (change: { userId: string; role: MemberRole; name: string }) =>
      api.setMemberRole(workspaceId as string, change.userId, change.role),
    onSuccess: async (_result, change) => {
      setSaid(`${change.name} is now ${article(roleLabel(change.role))}.`)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (member: { userId: string; name: string }) =>
      api.removeMember(workspaceId as string, member.userId),
    onSuccess: async (_result, member) => {
      setSaid(`${member.name} no longer has access.`)
      setRemoving(null)
      await refresh()
    },
  })

  if (!workspaceId) {
    return (
      <EmptyState icon="user" title="Just you">
        This is a single-user copy of Novel Engine, so there is nobody to invite.
      </EmptyState>
    )
  }

  const onInvite = (event: FormEvent) => {
    event.preventDefault()
    setCreated(null)
    invite.mutate()
  }
  const copy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.link)
      setCopied(true)
    } catch {
      // Clipboard access can be refused; the link is on screen to copy by hand.
      setCopied(false)
    }
  }
  const pending = (invitations.data ?? []).filter(
    (item) => !item.accepted_at && !item.revoked_at && new Date(item.expires_at).getTime() > openedAt,
  )
  const lastOwner = (members.data ?? []).filter((member) => member.role === 'owner').length <= 1

  return (
    <>
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p className="lede">
            Everyone here can see every project in this workspace. Editors can change the story;
            readers can only read it.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <section className="card" aria-labelledby="members-heading">
          <div className="card-head">
            <h2 id="members-heading">Members</h2>
          </div>
          {members.isPending && <Loading>Loading…</Loading>}
          <ErrorNotice error={members.error} prefix="Could not load members" />
          <ul className="rows">
            {(members.data ?? []).map((member) => {
              const name = member.display_name || member.email || 'A member'
              const you = member.user_id === userId
              const onlyOwner = member.role === 'owner' && lastOwner
              return (
                <li className="row" key={member.user_id}>
                  <span className="avatar sm tint-violet" aria-hidden="true">
                    {initial(name)}
                  </span>
                  <span className="grow">
                    <strong>{name}</strong> {you && <span className="muted">(you)</span>}
                    {member.email && member.email !== name && (
                      <span className="muted small" style={{ display: 'block' }}>
                        {member.email}
                      </span>
                    )}
                  </span>
                  {manage && !onlyOwner ? (
                    <select
                      aria-label={`Role of ${name}`}
                      style={{ width: 'auto' }}
                      value={member.role}
                      disabled={setMemberRole.isPending}
                      onChange={(event) =>
                        setMemberRole.mutate({
                          userId: member.user_id,
                          role: event.target.value as MemberRole,
                          name,
                        })
                      }
                    >
                      {ROLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Badge>{roleLabel(member.role)}</Badge>
                  )}
                  {manage && !you && !onlyOwner && (
                    <Button
                      size="sm"
                      variant="danger-quiet"
                      aria-label={`Remove ${name}`}
                      onClick={() => setRemoving({ userId: member.user_id, name })}
                    >
                      Remove
                    </Button>
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
          <ErrorNotice error={setMemberRole.error} prefix="Not changed" />
          {removing && (
            <ConfirmDialog
              title={`Remove ${removing.name}?`}
              confirmLabel={`Remove ${removing.name}`}
              keepLabel="Keep them"
              busy={remove.isPending}
              error={remove.error}
              onCancel={() => {
                remove.reset()
                setRemoving(null)
              }}
              onConfirm={() => remove.mutate(removing)}
            >
              <p>
                They lose access to every project in this workspace straight away. Nothing they
                wrote is removed, and you can invite them again later.
              </p>
            </ConfirmDialog>
          )}
          {manage && lastOwner && (
            <p className="hint" style={{ marginTop: '0.75rem' }}>
              A workspace always keeps at least one owner, so the only owner cannot be changed or
              removed.
            </p>
          )}
        </section>

        {!manage && access && (
          <p className="notice plain" role="note">
            Only an owner can invite people or change what they can do.
          </p>
        )}

        {manage && (
          <section className="card" aria-labelledby="invite-heading">
            <div className="card-head">
              <div>
                <h2 id="invite-heading">Invite someone</h2>
                <p className="muted small">
                  You get a private link to send them yourself. They sign in, open the link, and
                  join. Novel Engine does not send email.
                </p>
              </div>
            </div>
            <form className="stack" onSubmit={onInvite} noValidate>
              <div className="grid-2">
                <Field
                  label="Their email (optional)"
                  hint="If you add it, only an account with this address can use the link."
                >
                  {(props) => (
                    <input
                      {...props}
                      type="email"
                      autoComplete="off"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  )}
                </Field>
                <Field label="They will be" hint={ROLES.find((item) => item.value === role)?.can}>
                  {(props) => (
                    <select {...props} value={role} onChange={(event) => setRole(event.target.value as MemberRole)}>
                      {ROLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
              </div>
              <div className="actions">
                <Button variant="primary" type="submit" disabled={invite.isPending}>
                  {invite.isPending ? 'Creating…' : 'Create invitation link'}
                </Button>
              </div>
              {isApiError(invite.error) && invite.error.status === 422 ? (
                <p className="error-text" role="alert">
                  That does not look like an email address.
                </p>
              ) : (
                <ErrorNotice error={invite.error} prefix="The invitation was not created" />
              )}
            </form>

            {created && (
              <div className="notice ok" role="status" style={{ marginTop: '1rem' }}>
                <p>
                  <strong>Send this link{created.email ? ` to ${created.email}` : ''}.</strong> It is
                  shown only now and cannot be shown again. Anyone who has it
                  {created.email ? ' and signs in with that address' : ''} can join.
                </p>
                <Field label="Invitation link">
                  {(props) => (
                    <input {...props} readOnly value={created.link} onFocus={(event) => event.target.select()} />
                  )}
                </Field>
                <Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy link'}</Button>
              </div>
            )}
          </section>
        )}

        {manage && (
          <section className="card" aria-labelledby="pending-heading">
            <div className="card-head">
              <h2 id="pending-heading">Invitations not yet used</h2>
            </div>
            <ErrorNotice error={invitations.error} prefix="Could not load invitations" />
            {invitations.isSuccess && pending.length === 0 && (
              <p className="muted">None waiting.</p>
            )}
            <ul className="rows">
              {pending.map((item) => (
                <li className="row" key={item.id}>
                  <span className="grow">
                    <strong>{item.email ?? 'Anyone with the link'}</strong>
                    <span className="muted small" style={{ display: 'block' }} title={fullDate(item.expires_at)}>
                      Created {timeAgo(item.created_at)} · expires {timeAgo(item.expires_at)}
                    </span>
                  </span>
                  <Badge>{roleLabel(item.role)}</Badge>
                  <Button
                    size="sm"
                    variant="danger-quiet"
                    disabled={revoke.isPending}
                    aria-label={`Withdraw invitation for ${item.email ?? 'anyone with the link'}`}
                    onClick={() => revoke.mutate(item.id)}
                  >
                    Withdraw invitation
                  </Button>
                </li>
              ))}
            </ul>
            <ErrorNotice error={revoke.error} prefix="Not withdrawn" />
          </section>
        )}
      </div>
    </>
  )
}
