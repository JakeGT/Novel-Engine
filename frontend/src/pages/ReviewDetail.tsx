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
  type WorldTime,
} from '../api/client'
import { AskForChanges } from '../components/AskForChanges'
import { FromText } from '../components/FromText'
import { ChangeEditor, type Candidate } from '../components/ChangeEditor'
import { MutationImpact } from '../components/ChangeImpact'
import { ConversationEvidence, SetupSuggestions } from '../components/Conversations'
import { KeepDecisionNote } from '../components/KeptForYou'
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
import { isKeptNote } from '../lib/kept'
import { hasChangeForm } from '../lib/facts'
import { outcomeLabel } from '../lib/plan'
import { firmness } from '../lib/knowledge'
import { IMPORTANCE_KEY, NEEDS_DETAIL_KEY, TAGS_KEY, TRUST_KEY } from '../lib/marks'
import { useStoryClock, whenText } from '../lib/storytime'
import { fullDate, timeAgo } from '../lib/time'
import {
  CHANGE_STATES,
  REVIEW_REASONS,
  changeSetTitle,
  describeOperation,
  namesFromOperations,
  objectText,
  originLabel,
  PLAN_ROLES,
  planKindLabel,
  plural,
  predicateLabel,
  valueText,
  words,
} from '../lib/words'
import { actorLabel, scopedKey, useEntries, useInvalidateProject, useProject } from '../project'
import { ChangeIssues } from '../components/ReviewFindings'
import { useReviewFindings, type ReviewFindings } from '../lib/reviewFindings'

const OPEN = new Set(['draft', 'validating', 'needs_review', 'approved'])
// ``why`` is shown as its own line under the change, not as a field.
const HIDDEN_KEYS = new Set(['op', 'id', 'evidence', 'proposition', 'project_id', 'only_fills', 'base', 'why'])

type Issue = MutationValidation['issues'][number]

/** Changes of one kind are reviewed together, in the order they will be applied. */
const GROUPS: { label: string; one: string; types: string[] }[] = [
  { label: 'New entries', one: 'new entry', types: ['create_entity'] },
  { label: 'Changes to entries', one: 'change to an entry', types: ['update_entity'] },
  { label: 'Facts ended', one: 'fact ended', types: ['close_fact'] },
  { label: 'New facts', one: 'new fact', types: ['assert_fact'] },
  { label: 'When facts are true, and who knows', one: 'change to when or who knows', types: ['amend_fact'] },
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
  [TRUST_KEY]: 'How they take what others say',
}

const STANCE_WORDS: Record<string, string> = {
  believes: 'believes',
  doubts: 'doubts',
  disbelieves: 'does not believe',
}

/** A character's trust (KNW-006) in words: in general, then particular people. */
function trustWords(value: unknown, names: Map<string, string>): unknown {
  if (!isFlat(value)) return value
  const general = `${STANCE_WORDS[String(value.default)] ?? 'believes'} what people say`
  const speakers = isFlat(value.speakers) ? Object.entries(value.speakers) : []
  const particular = speakers.map(
    ([id, stance]) => `${STANCE_WORDS[String(stance)] ?? String(stance)} ${names.get(id) ?? 'someone'}`,
  )
  const text = [general, ...particular].join('; ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// Who takes part in an event, in words (KNW-005, KNW-006); other roles are shown as stored.
const PARTICIPANT_ROLES: Record<string, string> = {
  speaker: 'says it',
  listener: 'is told',
  overheard: 'overhears it',
  witness: 'sees it',
  present: 'is there but does not notice',
  narrator: 'narrates it',
  perceiver: 'hears it',
}

const EVENT_KINDS: Record<string, string> = {
  telling: 'Someone tells others something',
  witnessed: 'Something people see happen',
  claim: 'Something said, not necessarily true',
}

function participantWords(value: unknown, names: Map<string, string>): unknown {
  if (!Array.isArray(value)) return value
  return value
    .map((item) =>
      isFlat(item)
        ? `${names.get(String(item.entity_id)) ?? 'someone'} (${PARTICIPANT_ROLES[String(item.role)] ?? words(String(item.role)).toLowerCase()})`
        : String(item),
    )
    .join(', ')
}

const CLAIM_WORDS: Record<string, string> = {
  states: 'Says',
  suggests: 'Suggests',
  wonders: 'Wonders whether',
  denies: 'Says it is not so that',
}

/** What a claim (KNW-005) says, in words. Said, not necessarily true. */
function claimWords(value: unknown, names: Map<string, string>): unknown {
  if (!isFlat(value) || !isFlat(value.proposition)) return value
  const p = value.proposition
  const object = isFlat(p.object) ? objectText(p.object, names) : ''
  const sentence = `${names.get(String(p.subject_id)) ?? 'an entry'} ${predicateLabel(String(p.predicate ?? ''))} ${object}`
  return `${CLAIM_WORDS[String(value.modality ?? 'states')] ?? 'Says'} ${sentence.trim()} (said, not necessarily true)`
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
  const rows = Object.keys(PLAN_LABELS)
    .filter((key) => key !== 'details')
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
  return [...rows, ...planDetailRows(operation.payload.details, before?.details, names)]
}

// The plan's own parts kept in its notes, named as the plan form names them.
const PLAN_DETAIL_LABELS: Record<string, string> = {
  _intent: 'What the scene is for',
  _conditions: 'What must be true',
  _when: 'When in the story',
  _reveals: 'What the reader learns',
  _advances: 'Threads it moves along',
  _path: 'Stages',
}

/** A scene's intent (STY-003) in the plan card's words. */
function intentWords(value: unknown): unknown {
  if (!isFlat(value)) return value
  const parts = [
    value.goal ? `Wants: ${String(value.goal)}` : '',
    value.obstacle ? `In the way: ${String(value.obstacle)}` : '',
    value.outcome ? `How it ends: ${outcomeLabel(String(value.outcome)) ?? String(value.outcome)}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Nothing decided'
}

/** A scene's conditions (STY-005), each as a sentence with names, not ids. */
function conditionWords(value: unknown, names: Map<string, string>): unknown {
  if (!Array.isArray(value)) return value
  if (value.length === 0) return 'None'
  const sentences = value.map((item) => {
    if (!isFlat(item)) return String(item)
    const when = `${item.when === 'after' ? 'After' : 'Before'} (${item.strength === 'soft' ? 'preferred' : 'required'})`
    if (item.words) return `${when}: ${String(item.words)}`
    const check = isFlat(item.check) ? item.check : {}
    const pattern = isFlat(check.pattern) ? check.pattern : {}
    const who = names.get(String(item.entity_id)) ?? 'an entry'
    const other = pattern.other_id ? (names.get(String(pattern.other_id)) ?? 'something no longer there') : ''
    const what = `${predicateLabel(String(pattern.predicate ?? ''))} ${other}`.trim()
    const must = check.kind === 'lacks' ? 'must not have this' : 'must have this'
    return `${when}: ${who} ${must}: ${what}`
  })
  return sentences.join('; ')
}

/** One row for each part of a plan item's notes that changes. */
function planDetailRows(value: unknown, was: unknown, names: Map<string, string>): Row[] {
  if (!isFlat(value)) return []
  const before = isFlat(was) ? was : null
  // The plan keeps its own parts as JSON text; read them back before putting them in words.
  const parsed = (item: unknown): unknown => {
    if (typeof item !== 'string' || !/^\s*[[{]/.test(item)) return item
    try {
      return JSON.parse(item) as unknown
    } catch {
      return item
    }
  }
  const shown = (key: string, item: unknown) =>
    key === '_intent'
      ? intentWords(parsed(item))
      : key === '_conditions'
        ? conditionWords(parsed(item), names)
        : item
  return Object.entries(value)
    .filter(([key, item]) => !(before && same(before[key], item)))
    .map(([key, item]) => ({
      key: `details.${key}`,
      label: PLAN_DETAIL_LABELS[key] ?? (key.startsWith('_') ? words(key.slice(1)) : key),
      before: before ? (key in before ? shown(key, before[key]) : undefined) : undefined,
      after: shown(key, item),
    }))
}

// Story time and who may know a fact, in the author's words (ADR-020).
const TIME_LABELS: Record<string, string> = {
  starts: 'True from',
  ends: 'No longer true from',
  when: 'When',
}
const VISIBILITY_WORDS: Record<string, string> = {
  ordinary: 'Nothing said',
  common: 'Everyone in the story knows it',
  secret: 'A secret',
}

function fieldRows(
  operation: MutationOperation,
  names: Map<string, string>,
  describeTime: (value: WorldTime) => string = (value) => JSON.stringify(value),
): Row[] {
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
            before: was ? (name === TRUST_KEY ? trustWords(was[name], names) : was[name]) : undefined,
            after: name === TRUST_KEY ? trustWords(item, names) : item,
          }))
      }
      const was = before && key in before ? before[key] : undefined
      if (key in TIME_LABELS && isFlat(value)) {
        return [
          {
            key,
            label: TIME_LABELS[key],
            before: isFlat(was) ? describeTime(was as WorldTime) : was,
            after: describeTime(value as WorldTime),
          },
        ]
      }
      if (key === 'holder_entity_id') {
        return [{ key, label: 'Who believes it', before: was, after: names.get(String(value)) ?? 'someone' }]
      }
      if (key === 'source_ref') {
        // Usually the telling or sighting proposed with it, named by that event.
        const from = names.get(String(value)) ?? (value === 'author' ? 'Entered by an author' : 'Recorded elsewhere')
        return [{ key, label: 'Where it came from', before: was, after: from }]
      }
      if (key === 'participants') {
        return [{ key, label: 'Who takes part', before: participantWords(was, names), after: participantWords(value, names) }]
      }
      if (key === 'kind' && operation.operation_type === 'create_event' && typeof value === 'string' && value in EVENT_KINDS) {
        return [{ key, label: 'What it is', before: was, after: EVENT_KINDS[value] }]
      }
      if (key === 'claim') {
        return [{ key, label: 'What is said', before: was, after: claimWords(value, names) }]
      }
      if (key === 'confidence' && typeof value === 'number') {
        return [{ key, label: 'How firmly', before: was, after: firmness(value) }]
      }
      if (key === 'visibility') {
        return [
          {
            key,
            label: 'Who knows',
            before: was === undefined ? undefined : (VISIBILITY_WORDS[String(was)] ?? was),
            after: VISIBILITY_WORDS[String(value)] ?? value,
          },
        ]
      }
      return [{ key, label: words(key), before: was, after: value }]
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
  reviewed = null,
}: {
  operation: MutationOperation
  title: string
  issues: Issue[]
  /** The advisory findings and their dismissals (CON-016). */
  reviewed?: ReviewFindings | null
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
  const { scope } = useProject()
  const clock = useStoryClock(scope)
  const rows = fieldRows(operation, names, (value) => whenText(value, clock.unit, clock.eventName))
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
      {typeof operation.payload.why === 'string' && operation.payload.why && (
        <p className="small">
          <span className="eyebrow">Why</span> {operation.payload.why}
        </p>
      )}
      {skipped && <p className="muted small">Skipped — this will not be added.</p>}
      {operation.decision?.note && <p className="muted small">Note: “{operation.decision.note}”</p>}
      {gone && (
        <p className="notice warn" role="note">
          The thing this change was about no longer exists in your story.
        </p>
      )}

      <ChangeIssues issues={issues} reviewed={reviewed} editable={editable} />

      <FromText evidence={(operation.payload as { evidence?: { source_artefact_id: string; location?: string | null }[] }).evidence} />

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

/** One set of suggested changes: read it as sentences, keep or skip each, then add it to the story. */
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
  // The advisory findings with the author's dismissals, read with the same check (CON-016).
  const reviewed = useReviewFindings(
    mutationId,
    Boolean(pending) && OPEN.has(pending?.status ?? '') && canWrite,
    [
      (pending?.operations ?? []).map((item) => `${item.ordinal}.${item.revision}`).join('|'),
      pending?.head_revision,
      checked.dataUpdatedAt,
    ].join(':'),
  )

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
  // A story rule the author set to block holds the set back until it is fixed (CON-015).
  const ruleBlocked = (findings?.issues ?? []).some(
    (issue) => issue.severity === 'blocking' && issue.code.startsWith('rule:'),
  )
  const canCommit = editable && !change.is_stale && included.length > 0 && !ruleBlocked
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
        {/* Kept for the author under one of their rules, or why it waited (AST-EXP-015). */}
        {change.origin === 'model' && <KeepDecisionNote mutationId={change.id} />}
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
                {/* Offered by need (Level 5): a value replaced may have been true until a
                    certain time, and then both can be kept, each for its own stretch. */}
                {notes.some((item) => item.includes('only one thing at a time')) && (
                  <p className="hint small">
                    If the old one was true until a certain time, you can keep both instead: ask
                    again saying when it changed (“she moved to Saltmere in year 10”), or give each
                    fact its time under “When and who knows”. The{' '}
                    <Link to={`/projects/${scope.projectId}/timeline`}>Timeline</Link> shows the
                    result.
                  </p>
                )}
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
                Added to {planOnly ? 'your plan' : 'your story'} as version {committed?.revision ?? change.committed_revision}.
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
              , so it cannot be added as it stands.{' '}
              {(change.changed_since ?? []).length === 0
                ? 'Nothing it touches has changed since, so updating moves it onto the new version as it is, keeping any approval.'
                : `Since then, ${(change.changed_since ?? [])
                    .map((id) => names.get(id) ?? 'something it touches')
                    .join(', ')} changed, so updating keeps every change and decision and asks for review again.`}
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
                      reviewed={reviewed}
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

        {/* Before the decision: what depends on what these changes touch (CON-017). */}
        {open && !done && (
          <MutationImpact
            mutationId={mutationId}
            version={change.operations
              .map((item) => `${item.ordinal}.${item.revision}.${item.decision?.decision ?? ''}`)
              .join('|')}
          />
        )}

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
                {ruleBlocked
                  ? 'Nothing can be added until the problem above is fixed'
                  : included.length === change.operations.length
                    ? included.length === 1
                      ? 'The change will be added'
                      : `All ${plural(included.length, 'change')} will be added`
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
                  {change.is_stale ? (
                    'Update to the latest version first.'
                  ) : ruleBlocked ? (
                    <>
                      A change breaks a story rule you set to block. Skip or edit that change, or{' '}
                      <Link to={`/projects/${scope.projectId}/rules`}>set the rule to warn</Link>.
                    </>
                  ) : (
                    'Every change is skipped, so there is nothing to add.'
                  )}
                </span>
              )}
              <Button
                variant="primary"
                icon="check"
                disabled={busy || !canCommit}
                onClick={() => commit.mutate(needsApproval)}
              >
                {commit.isPending ? 'Adding…' : `${needsApproval ? 'Approve and add' : 'Add'} to ${planOnly ? 'the plan' : 'your story'}`}
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
          {change.decisions.map((decision, index) =>
            // Kept under the author's own rule: no one approved it by hand, so it says so.
            isKeptNote(decision.note) ? (
              <p className="small muted" key={`d${index}`}>
                {decision.note}
              </p>
            ) : (
              <p className="small muted" key={`d${index}`}>
                {words(decision.decision)}
                {decision.actor_user_id ? ` by ${actorLabel(decision.actor_user_id, userId)}` : ''}
                {decision.note ? ` — “${decision.note}”` : ''}
              </p>
            ),
          )}
        </details>
      </div>
    </>
  )
}
