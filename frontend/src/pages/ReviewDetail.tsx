import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import {
  api,
  errorMessage,
  isStaleHead,
  type MutationDetail as Detail,
  type MutationOperation,
  type MutationValidation,
} from '../api/client'
import { AskForChanges } from '../components/AskForChanges'
import { ChangeEditor, type Candidate } from '../components/ChangeEditor'
import { ConversationEvidence, SetupSuggestions } from '../components/Conversations'
import { Icon } from '../components/icons'
import { RestoreDialog } from '../components/RestoreDialog'
import {
  ConfirmDialog,
  Dialog,
  Badge,
  Button,
  ChangeStateBadge,
  ErrorNotice,
  Field,
  Json,
  Loading,
  ReadOnlyNotice,
} from '../components/ui'
import { useConversationFor } from '../lib/conversations'
import { hasChangeForm } from '../lib/facts'
import { IMPORTANCE_KEY, NEEDS_DETAIL_KEY, TAGS_KEY } from '../lib/marks'
import { fullDate, timeAgo } from '../lib/time'
import {
  CHANGE_STATES,
  REVIEW_REASONS,
  changeSetTitle,
  describeOperation,
  namesFromOperations,
  originLabel,
  PLAN_ROLES,
  planKindLabel,
  plural,
  valueText,
  words,
} from '../lib/words'
import { actorLabel, scopedKey, useEntries, useInvalidateProject, useProject } from '../project'

const OPEN = new Set(['draft', 'validating', 'needs_review', 'approved'])
const HIDDEN_KEYS = new Set(['op', 'id', 'evidence', 'proposition', 'project_id'])

type Issue = MutationValidation['issues'][number]

/** Changes of one kind are reviewed together, in the order they will be applied. */
const GROUPS: { label: string; one: string; types: string[] }[] = [
  { label: 'New entries', one: 'new entry', types: ['create_entity'] },
  { label: 'Changes to entries', one: 'change to an entry', types: ['update_entity'] },
  { label: 'Facts ended', one: 'fact ended', types: ['close_fact'] },
  { label: 'New facts', one: 'new fact', types: ['assert_fact'] },
  { label: 'Beliefs ended', one: 'belief ended', types: ['close_belief'] },
  { label: 'New beliefs', one: 'new belief', types: ['assert_belief'] },
  { label: 'Events', one: 'event', types: ['create_event', 'amend_event', 'retire_event'] },
  // What the author means to happen. Kept as its own group: it is not part of the story bible.
  { label: 'Story plan', one: 'part of the plan', types: ['create_plan_item', 'update_plan_item', 'retire_plan_item'] },
  { label: 'Archived', one: 'archived', types: ['retire_entity'] },
]

function groupOperations(
  operations: MutationOperation[],
): { label: string; one: string; items: MutationOperation[] }[] {
  const known = new Set(GROUPS.flatMap((group) => group.types))
  const groups = GROUPS.map((group) => ({
    label: group.label,
    one: group.one,
    items: operations.filter((operation) => group.types.includes(operation.operation_type)),
  }))
  groups.push({
    label: 'Other changes',
    one: 'other change',
    items: operations.filter((operation) => !known.has(operation.operation_type)),
  })
  return groups.filter((group) => group.items.length > 0)
}

/** Field-by-field rows for a change: what it sets, and what was there before when it edits. */
// Marks are stored beside details under reserved names; an author sees these words.
const MARK_LABELS: Record<string, string> = {
  [TAGS_KEY]: 'Tags',
  [IMPORTANCE_KEY]: 'Importance',
  [NEEDS_DETAIL_KEY]: 'Marked to fill in later',
}

type Row = { key: string; label: string; before?: unknown; after: unknown }

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const isFlat = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const PLAN_OPERATIONS = new Set(['create_plan_item', 'update_plan_item', 'retire_plan_item'])
const PLAN_LABELS: Record<string, string> = {
  kind: 'What it is',
  title: 'Title',
  summary: 'What happens',
  parent_id: 'Under',
  refs: 'Who and where',
  sets_up: 'Opens',
  pays_off: 'Resolves',
  details: 'Notes',
}

/** A part of the plan, read as words: names instead of ids, roles in plain terms. */
function planRows(operation: MutationOperation, names: Map<string, string>): Row[] {
  const before = operation.target_before ?? null
  const name = (id: unknown) => names.get(String(id)) ?? 'something no longer there'
  const shown = (key: string, value: unknown): unknown => {
    if (value === null || value === undefined) return value
    if (key === 'kind') return planKindLabel(String(value))
    if (key === 'parent_id') return name(value)
    if (key === 'refs' && Array.isArray(value)) {
      return value.map((ref) => `${name(ref.entity_id)} (${PLAN_ROLES[String(ref.role)] ?? String(ref.role)})`)
    }
    if ((key === 'sets_up' || key === 'pays_off') && Array.isArray(value)) return value.map(name)
    return value
  }
  return Object.keys(PLAN_LABELS)
    .filter((key) => {
      const value = operation.payload[key]
      if (value === null || value === undefined || value === '') return false
      if (Array.isArray(value) && value.length === 0 && !before) return false
      if (isFlat(value) && Object.keys(value).length === 0) return false
      return !before || !same(before[key], value)
    })
    .map((key) => ({
      key,
      label: PLAN_LABELS[key],
      before: before ? shown(key, before[key]) : undefined,
      after: shown(key, operation.payload[key]),
    }))
}

function fieldRows(operation: MutationOperation, names: Map<string, string>): Row[] {
  if (PLAN_OPERATIONS.has(operation.operation_type)) return planRows(operation, names)
  const before = operation.target_before ?? null
  return Object.entries(operation.payload)
    .filter(([key, value]) => !HIDDEN_KEYS.has(key) && value !== null && value !== undefined)
    .filter(([, value]) => !(Array.isArray(value) && value.length === 0))
    .filter(([, value]) => !(isFlat(value) && Object.keys(value).length === 0))
    .filter(([key, value]) => !before || !(key in before) || !same(before[key], value))
    .flatMap(([key, value]): Row[] => {
      // Details get a row each: one long run-on line cannot be compared or checked.
      if (key === 'attributes' && isFlat(value)) {
        const was = before && isFlat(before[key]) ? before[key] : null
        return Object.entries(value)
          .filter(([name, item]) => !was || !same(was[name], item))
          .map(([name, item]) => ({
            key: `attributes.${name}`,
            label: MARK_LABELS[name] ?? words(name),
            before: was ? was[name] : undefined,
            after: item,
          }))
      }
      return [{ key, label: words(key), before: before && key in before ? before[key] : undefined, after: value }]
    })
}

function ChangeCard({
  operation,
  title,
  issues,
  candidates,
  names,
  editable,
  busy,
  onDecide,
  onRevise,
}: {
  operation: MutationOperation
  title: string
  issues: Issue[]
  candidates: Candidate[]
  /** Names of entries and titles of plan items, by id. */
  names: Map<string, string>
  editable: boolean
  busy: boolean
  onDecide: (decision: 'accepted' | 'rejected', note: string | null) => void
  onRevise: (payload: Record<string, unknown>, reason: string | null) => Promise<unknown>
}) {
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState(false)
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const number = operation.ordinal + 1
  const decision = operation.decision?.decision
  const skipped = decision === 'rejected'
  const rows = fieldRows(operation, names)
  const isEdit = Boolean(operation.target_before && Object.keys(operation.target_before).length > 0)
  const gone = operation.target_before !== null && operation.target_before !== undefined && !isEdit

  const save = async () => {
    let payload: unknown
    try {
      payload = JSON.parse(text)
    } catch (failure) {
      return setProblem(`Not valid JSON: ${errorMessage(failure)}`)
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return setProblem('The change must be a JSON object.')
    }
    try {
      await onRevise(payload as Record<string, unknown>, reason.trim() || null)
      setProblem(null)
      setEditing(false)
    } catch (failure) {
      // Keep the editor open with what was typed.
      setProblem(errorMessage(failure))
    }
  }

  return (
    <li className={`change ${skipped ? 'skipped' : ''}`}>
      <div className="change-head">
        <span className="change-title">{title}</span>
        {operation.revision > 1 && <Badge tone="info">Edited</Badge>}
        {editable && hasChangeForm(operation.operation_type) && !form && (
          <Button size="sm" variant="ghost" icon="edit" aria-label={`Edit change ${number}: ${title}`} onClick={() => setForm(true)}>
            Edit
          </Button>
        )}
        {editable ? (
          <div className="segmented" role="group" aria-label={`Change ${number}: ${title}`}>
            <button
              type="button"
              aria-pressed={decision === 'accepted'}
              disabled={busy}
              onClick={() => onDecide('accepted', note.trim() || null)}
            >
              Keep
            </button>
            <button
              type="button"
              aria-pressed={skipped}
              disabled={busy}
              onClick={() => onDecide('rejected', note.trim() || null)}
            >
              Skip
            </button>
          </div>
        ) : (
          <Badge tone={skipped ? 'danger' : decision === 'accepted' ? 'ok' : 'neutral'}>
            {skipped ? 'Skipped' : decision === 'accepted' ? 'Kept' : 'Included'}
          </Badge>
        )}
      </div>
      {skipped && <p className="muted small">Skipped — this will not be added.</p>}
      {operation.decision?.note && <p className="muted small">Note: “{operation.decision.note}”</p>}
      {gone && (
        <p className="notice warn" role="note">
          The thing this change was about no longer exists in your story.
        </p>
      )}

      {issues.map((issue, index) => (
        <p
          key={index}
          className={`notice ${issue.severity === 'blocking' ? 'danger' : 'plain'}`}
          role={issue.severity === 'blocking' ? 'alert' : 'note'}
        >
          <span>
            <strong>{issue.severity === 'blocking' ? 'This must be fixed first' : 'Worth a look'}:</strong>{' '}
            {issue.message}
          </span>
        </p>
      ))}

      {form && (
        <ChangeEditor
          operation={operation}
          candidates={candidates}
          busy={busy}
          onSave={(payload) => onRevise(payload, null)}
          onCancel={() => setForm(false)}
        />
      )}
      {!form && rows.length > 0 && (
        <div className="table-wrap">
          <table className="diff-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Field</span>
                </th>
                {isEdit && <th scope="col">Now</th>}
                <th scope="col">{isEdit ? 'Would become' : 'Value'}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{row.label}</th>
                  {isEdit && <td className="was">{valueText(row.before)}</td>}
                  <td className="now">{valueText(row.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="more">
        <summary>More about change {number}</summary>
        <div className="stack">
          {isEdit && (
            <div>
              <p className="label">In your story now</p>
              <Json value={operation.target_before} />
            </div>
          )}
          <div>
            <p className="label">The change as stored</p>
            <Json value={operation.payload} />
          </div>
          {editable && !editing && (
            <>
              <Field
                label={`Note for change ${number} (optional)`}
                hint="Saved with your Keep or Skip decision."
              >
                {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
              </Field>
              <div>
                <Button
                  size="sm"
                  icon="edit"
                  onClick={() => {
                    setText(JSON.stringify(operation.payload, null, 2))
                    setEditing(true)
                  }}
                >
                  Edit change {number} as stored
                </Button>
              </div>
            </>
          )}
          {editable && editing && (
            <>
              <Field
                label={`Change ${number} (JSON)`}
                hint="Saving keeps the original and any decision on it as a record, and asks for review again."
                error={problem}
              >
                {(props) => (
                  <textarea
                    {...props}
                    className="code"
                    rows={10}
                    spellCheck={false}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                  />
                )}
              </Field>
              <Field label="Reason for the edit (optional)">
                {(props) => <input {...props} value={reason} onChange={(event) => setReason(event.target.value)} />}
              </Field>
              <div className="actions">
                <Button variant="primary" disabled={busy} onClick={() => void save()}>
                  Save edited change
                </Button>
                <Button variant="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </>
          )}
        </div>
      </details>
    </li>
  )
}

/** One set of suggested changes: read it as sentences, keep or skip each, then add it to canon. */
export function ReviewDetail() {
  const { scope, canWrite, userId, headRevision } = useProject()
  const { mutationId = '' } = useParams()
  const location = useLocation()
  const invalidate = useInvalidateProject(scope)
  const entries = useEntries(scope)
  const arrival = (location.state ?? {}) as {
    fromAssistant?: boolean
    understood?: string
    notes?: string[]
    discarded?: { kind: string; reason: string }[]
  }

  const detail = useQuery({
    queryKey: scopedKey(scope, 'mutation', mutationId),
    queryFn: () => api.getMutation(scope, mutationId),
  })
  // Titles of what is already planned, so a change to the plan reads in words.
  const planned = useQuery({
    queryKey: scopedKey(scope, 'plan'),
    queryFn: () => api.outline(scope),
  })
  const [note, setNote] = useState('')
  const [validation, setValidation] = useState<MutationValidation | null>(null)
  // Checked against the story as soon as the page opens, and again whenever a change is
  // decided or edited, so problems show on the changes without being asked for.
  const pending = detail.data
  const checked = useQuery({
    queryKey: scopedKey(
      scope,
      'mutation-check',
      mutationId,
      (pending?.operations ?? [])
        .map((item) => `${item.ordinal}.${item.revision}.${item.decision?.decision ?? ''}`)
        .join('|'),
      pending?.head_revision,
    ),
    queryFn: () => api.validateMutation(scope, mutationId),
    enabled: Boolean(pending) && OPEN.has(pending?.status ?? '') && canWrite,
    retry: false,
  })
  const [committed, setCommitted] = useState<{ revision: number } | null>(null)

  // Every review action returns the fresh change set; reload everything that depends on it.
  const session = useConversationFor(mutationId)
  const [discarding, setDiscarding] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const [undone, setUndone] = useState<number | null>(null)
  const action = useMutation({
    mutationFn: (run: () => Promise<Detail>) => run(),
    onSuccess: async () => {
      setValidation(null)
      await invalidate()
    },
  })
  const validate = useMutation({
    mutationFn: () => api.validateMutation(scope, mutationId),
    onSuccess: (result) => setValidation(result),
  })
  // Approval and the commit are separate, recorded steps on the server; one button
  // runs the approval when it is still needed and then the commit.
  const [announce, setAnnounce] = useState(false)
  const commit = useMutation({
    mutationFn: async (needsApproval: boolean) => {
      if (needsApproval) await api.approveMutation(scope, mutationId, note.trim() || null)
      return api.commitMutation(scope, mutationId)
    },
    // Only a success from the commit call makes this canon.
    onSuccess: async (result) => {
      setCommitted({ revision: result.revision })
      // Said in a dialog: the notice at the top of a long page is out of view from here.
      setAnnounce(true)
      await invalidate()
    },
    onError: () => invalidate(),
  })

  const change = detail.data
  const back = (
    <Link className="crumb" to={`/projects/${scope.projectId}/review`}>
      <Icon name="arrowLeft" size={16} /> Review
    </Link>
  )
  if (!change) {
    return (
      <>
        {back}
        {detail.isPending && <Loading>Opening the suggested changes…</Loading>}
        <ErrorNotice error={detail.error} prefix="Could not open these changes" />
      </>
    )
  }

  const open = OPEN.has(change.status)
  const editable = open && canWrite
  const included = change.operations.filter((operation) => operation.decision?.decision !== 'rejected')
  const findings = validation ?? checked.data ?? null
  const issuesFor = (ordinal: number) =>
    (findings?.issues ?? []).filter((issue) => issue.ordinal === ordinal)
  const general = issuesFor(-1)
  const busy = action.isPending || commit.isPending
  const needsApproval = change.status !== 'approved' && (change.requires_review || change.status !== 'draft')
  const canCommit = editable && !change.is_stale && included.length > 0
  const names = new Map([
    ...entries.names,
    ...(planned.data?.items ?? []).map((item) => [item.id, item.title] as const),
    ...namesFromOperations(change.operations),
  ])
  const groups = groupOperations(change.operations)
  // A set that only touches the plan adds intent, not fact, and is worded that way.
  const planOnly = included.length > 0 && included.every((item) => PLAN_OPERATIONS.has(item.operation_type))
  // Where to go once it is added: back to the plan, or to what the story bible needs next.
  const afterwards = planOnly
    ? { to: `/projects/${scope.projectId}/plan`, label: 'Open the story plan' }
    : { to: `/projects/${scope.projectId}`, label: 'See what to fill in next' }
  // What a fact can be about or point to: the story's entries and the ones this set would add.
  const candidates: Candidate[] = [
    ...entries.items
      .filter((item) => item.lifecycle === 'active')
      .map((item) => ({ id: item.id, name: item.name, type: item.type })),
    ...change.operations
      .filter((item) => item.operation_type === 'create_entity' && item.decision?.decision !== 'rejected')
      .map((item) => ({
        id: String(item.payload.id),
        name: String(item.payload.name),
        type: String(item.payload.type),
      })),
  ]
  // One decision for a whole group: only the changes that differ are sent, in one request.
  const decideAll = (items: MutationOperation[], decision: 'accepted' | 'rejected') =>
    action.mutate(async () => {
      const decisions = items
        .filter((item) => item.decision?.decision !== decision)
        .map((item) => ({ ordinal: item.ordinal, decision }))
      return decisions.length > 0 ? api.decideOperations(scope, mutationId, decisions) : change
    })
  const done = committed !== null || change.status === 'committed'
  // Shown on arrival from the assistant, and again later from the kept conversation.
  const understood = arrival.understood || session.data?.understood || ''
  const notes = arrival.notes?.length ? arrival.notes : (session.data?.notes ?? [])
  const leftOut = arrival.discarded ?? []
  const addedAt = committed?.revision ?? change.committed_revision ?? null

  return (
    <>
      {back}
      <div className="page-head">
        <div>
          <h1>{changeSetTitle(change)}</h1>
          <p className="lede">
            {plural(change.operations.length, 'suggested change')} · started{' '}
            <span title={fullDate(change.created_at)}>{timeAgo(change.created_at)}</span>
            {change.actor_user_id ? ` by ${actorLabel(change.actor_user_id, userId)}` : ''} · from{' '}
            {originLabel(change.origin)}
          </p>
        </div>
        <ChangeStateBadge status={change.status} />
      </div>

      <div className="section-gap">
        {/* One notice says where things stand; the changes themselves come straight after. */}
        {open && (
          <div className="notice" role="status">
            <p>
              <strong>{planOnly ? 'Not part of your plan yet.' : 'Not part of your story yet.'}</strong>{' '}
              {understood ? `The assistant understood: ${understood} ` : ''}
              {change.requires_review
                ? `It needs your approval because ${change.review_reasons
                    .map((reason) => REVIEW_REASONS[reason] ?? reason)
                    .join(' and ')}.`
                : 'It is a small change by an author, so it can be added straight away.'}
            </p>
            {notes.length > 0 && (
              <div>
                <p>
                  <strong>Worth knowing before you decide:</strong>
                </p>
                <ul>
                  {notes.map((item, index) => (
                    <li key={index}>• {item}</li>
                  ))}
                </ul>
              </div>
            )}
            {leftOut.length > 0 && (
              <details className="more">
                <summary>
                  {leftOut.length === 1
                    ? 'One idea could not be turned into a valid change and was left out'
                    : `${leftOut.length} ideas could not be turned into valid changes and were left out`}
                </summary>
                <ul>
                  {leftOut.map((item, index) => (
                    <li key={index}>
                      {words(item.kind)}: {item.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {done ? (
          <p className="notice ok" role="status">
            <span>
              <strong>
                Added to {planOnly ? 'your plan' : 'canon'} as version {committed?.revision ?? change.committed_revision}.
              </strong>{' '}
              These changes are now part of your {planOnly ? 'plan' : 'story'}.
            </span>
            <Link className="btn" to={afterwards.to}>
              {afterwards.label}
            </Link>
          </p>
        ) : null}
        {announce && committed && (
          <Dialog
            title={planOnly ? 'Added to your plan' : 'Added to your story'}
            onClose={() => {
              setAnnounce(false)
              window.scrollTo({ top: 0 })
            }}
          >
            <div className="stack">
              <p>
                {included.length === 1 ? 'The change is' : `All ${included.length} changes are`} now
                part of your {planOnly ? 'plan' : 'story'}, saved as version {committed.revision}. You can undo this from
                this page.
              </p>
              <div className="actions end">
                <Button
                  variant="ghost"
                  onClick={() => {
                    setAnnounce(false)
                    window.scrollTo({ top: 0 })
                  }}
                >
                  Stay on this page
                </Button>
                <Link className="btn primary" data-autofocus to={afterwards.to}>
                  {afterwards.label}
                </Link>
              </div>
            </div>
          </Dialog>
        )}
        {done && canWrite && (
          // Taking it back is offered where it was added; it is exact only while nothing came after.
          <div className="actions">
            {undone !== null ? (
              <p className="notice plain" role="status">
                Undone. Your story is back as it was, saved as version {undone}.
              </p>
            ) : addedAt !== null && addedAt === headRevision ? (
              <>
                <Button icon="undo" onClick={() => setUndoing(true)}>
                  Undo these changes
                </Button>
                <span className="hint">Takes back everything this added, as a new version.</span>
              </>
            ) : (
              <span className="hint">
                Other changes have been saved since, so this can no longer be undone on its own.{' '}
                <Link to={`/projects/${scope.projectId}/history`}>Go back to an earlier version in History.</Link>
              </span>
            )}
          </div>
        )}
        {undoing && addedAt !== null && (
          <RestoreDialog
            undoing
            revision={addedAt - 1}
            onClose={() => setUndoing(false)}
            onDone={(revision) => setUndone(revision)}
          />
        )}
        {done ? (
          <></>
        ) : change.status === 'rejected' ? (
          <p className="notice danger" role="status">
            These changes were discarded. None of them are part of your story, and they can no
            longer be changed.
          </p>
        ) : null}

        {change.is_stale && (
          <div className="notice warn" role="alert">
            <p>
              <strong>Your story has moved on since this was written.</strong> It was based on
              version {change.expected_revision}, and you are now on version {change.head_revision}
              , so it cannot be added as it stands. Updating keeps every change and decision, and
              asks for review again.
            </p>
            {editable && (
              <Button
                disabled={busy}
                onClick={() =>
                  action.mutate(() => api.retargetMutation(scope, mutationId, change.head_revision))
                }
              >
                Update to version {change.head_revision}
              </Button>
            )}
          </div>
        )}
        <ReadOnlyNotice canWrite={canWrite} />
        <ErrorNotice error={action.error} />

        {/* Before the changes: drafting again with these replaces this draft, so the
            choice has to come before any time is spent deciding on what is below. */}
        {session.data && session.data.suggestions.length > 0 && <SetupSuggestions session={session.data} />}

        <section aria-labelledby="changes-heading" className="stack">
          <div className="actions" style={{ justifyContent: 'space-between' }}>
            <h2 id="changes-heading">The changes</h2>
            {editable && (
              <Button size="sm" icon="shield" disabled={validate.isPending} onClick={() => validate.mutate()}>
                {validate.isPending ? 'Checking…' : 'Check for problems'}
              </Button>
            )}
          </div>
          {editable && (
            <AskForChanges
              mutationId={mutationId}
              onRevised={async () => {
                setValidation(null)
                await invalidate()
              }}
            />
          )}
          <ErrorNotice error={validate.error} prefix="Could not check" />
          {/* A clean result is only announced when it was asked for. */}
          {findings && (validation || !findings.ok) && (
            <div className={`notice ${findings.ok ? 'ok' : 'danger'}`} role="status">
              <p>
                <strong>
                  {findings.ok
                    ? 'No problems found.'
                    : 'There are problems that would stop these being added.'}
                </strong>{' '}
                This is a preview; everything is checked again when you add it.
              </p>
              {general.map((issue, index) => (
                <p key={index}>{issue.message}</p>
              ))}
            </div>
          )}
          {groups.length > 1 && (
            <p className="muted small" role="status">
              {groups
                .map((group) =>
                  group.items.length === 1 ? `1 ${group.one}` : `${group.items.length} ${group.label.toLowerCase()}`,
                )
                .join(' · ')}
            </p>
          )}
          {groups.map((group) => {
            const kept = group.items.filter((item) => item.decision?.decision !== 'rejected').length
            return (
              <section key={group.label} aria-label={group.label} className="stack tight">
                {groups.length > 1 && (
                  <div className="actions" style={{ justifyContent: 'space-between', marginTop: '0.75rem' }}>
                    <h3 className="eyebrow">
                      {group.label} · {kept} of {group.items.length} kept
                    </h3>
                    {editable && group.items.length > 1 && (
                      <span className="actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={`Keep all ${group.label.toLowerCase()}`}
                          onClick={() => decideAll(group.items, 'accepted')}
                        >
                          Keep all
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={`Skip all ${group.label.toLowerCase()}`}
                          onClick={() => decideAll(group.items, 'rejected')}
                        >
                          Skip all
                        </Button>
                      </span>
                    )}
                  </div>
                )}
                <ol>
                  {group.items.map((operation) => (
                    <ChangeCard
                      key={`${operation.ordinal}-${operation.revision}`}
                      operation={operation}
                      title={describeOperation(operation, names)}
                      issues={issuesFor(operation.ordinal)}
                      candidates={candidates}
                      names={names}
                      editable={editable}
                      busy={busy}
                      onDecide={(decision, operationNote) =>
                        action.mutate(() =>
                          api.decideOperation(scope, mutationId, operation.ordinal, decision, operationNote),
                        )
                      }
                      onRevise={async (payload, reason) => {
                        // A failed edit is reported inside the editor, which keeps the text.
                        await api.reviseOperation(scope, mutationId, operation.ordinal, payload, reason)
                        setValidation(null)
                        await invalidate()
                      }}
                    />
                  ))}
                </ol>
              </section>
            )
          })}
        </section>

        {commit.isError &&
          (isStaleHead(commit.error) ? (
            <p className="notice warn" role="alert">
              <span>
                <strong>Not added.</strong> Your story changed while you were reviewing. Update
                these changes to the latest version above, then look them over again.
              </span>
            </p>
          ) : (
            <ErrorNotice error={commit.error} prefix="Not added" />
          ))}

        {editable && (
          <section className="sticky-bar" aria-label="Decision">
            <div style={{ flex: '1 1 14rem' }}>
              <strong>
                {included.length === change.operations.length
                  ? `All ${plural(included.length, 'change')} will be added`
                  : `${included.length} of ${plural(change.operations.length, 'change')} will be added`}
              </strong>
              <details className="more">
                <summary>Add a note</summary>
                <Field label="Review note (optional)">
                  {(props) => <input {...props} value={note} onChange={(event) => setNote(event.target.value)} />}
                </Field>
              </details>
            </div>
            <div className="actions">
              {!canCommit && (
                <span className="hint">
                  {change.is_stale
                    ? 'Update to the latest version first.'
                    : 'Every change is skipped, so there is nothing to add.'}
                </span>
              )}
              <Button
                variant="primary"
                icon="check"
                disabled={busy || !canCommit}
                onClick={() => commit.mutate(needsApproval)}
              >
                {commit.isPending ? 'Adding…' : `${needsApproval ? 'Approve and add' : 'Add'} to ${planOnly ? 'the plan' : 'canon'}`}
              </Button>
            </div>
          </section>
        )}

        {/* After the changes: the record of where they came from. */}
        <ErrorNotice error={session.error} prefix="Could not load where this came from" />
        {session.data && <ConversationEvidence session={session.data} />}

        {/* Kept apart from the main action: discarding cannot be taken back. */}
        {editable && (
          <section className="danger-zone" aria-labelledby="discard-heading">
            <div>
              <h2 id="discard-heading">Don’t want any of this?</h2>
              <p className="muted small">
                Discarding throws these suggested changes away. Your story stays as it is.
              </p>
            </div>
            <Button variant="danger-quiet" disabled={busy} onClick={() => setDiscarding(true)}>
              Discard these changes
            </Button>
          </section>
        )}

        {discarding && (
          <ConfirmDialog
            title={`Discard ${plural(change.operations.length, 'suggested change')}?`}
            confirmLabel="Discard changes"
            keepLabel="Keep reviewing"
            busy={busy}
            error={action.error}
            onCancel={() => setDiscarding(false)}
            onConfirm={() =>
              action.mutate(() => api.rejectMutation(scope, mutationId, note.trim() || null), {
                onSuccess: () => setDiscarding(false),
              })
            }
          >
            <p>
              None of them will be added to your story, and they cannot be brought back. Nothing
              already in your story changes.
            </p>
          </ConfirmDialog>
        )}

        <details className="more">
          <summary>Review log</summary>
          {change.transitions.length === 0 && change.decisions.length === 0 && (
            <p className="muted">No review activity yet.</p>
          )}
          <ol className="timeline">
            {change.transitions.map((transition, index) => (
              <li key={`t${index}`}>
                <strong>{(CHANGE_STATES[transition.to_status]?.label ?? transition.to_status)}</strong>{' '}
                <span className="muted">
                  {transition.actor_user_id ? `· ${actorLabel(transition.actor_user_id, userId)}` : ''}
                </span>
                <div className="when" title={fullDate(transition.created_at)}>
                  {timeAgo(transition.created_at)}
                  {transition.reason ? ` · ${transition.reason}` : ''}
                </div>
              </li>
            ))}
          </ol>
          {change.decisions.map((decision, index) => (
            <p className="small muted" key={`d${index}`}>
              {words(decision.decision)}
              {decision.actor_user_id ? ` by ${actorLabel(decision.actor_user_id, userId)}` : ''}
              {decision.note ? ` — “${decision.note}”` : ''}
            </p>
          ))}
        </details>
      </div>
    </>
  )
}
