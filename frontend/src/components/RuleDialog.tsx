import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type CanonEntity,
  type RuleBody,
  type RuleCheck,
  type RuleExample,
  type RulePattern,
  type StoryRule,
} from '../api/client'
import {
  checkFor,
  emptyPattern,
  KIND_GROUPS,
  KINDS,
  rangeNumber,
  splitChoice,
  TIMES,
  type CheckKind,
  type RuleCheckShape,
  type RuleTime,
} from '../lib/rules'
import { predicateLabel, typeLabel } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Field, Loading } from './ui'

function startBody(predicate: string): RuleBody {
  return {
    form: 1,
    applies_to: {},
    mode: 'all',
    time: 'as_recorded',
    checks: [{ kind: 'has', count: 1, pattern: emptyPattern(predicate) }],
  }
}

/** Who a rule does not hold for: sure breaks, and those the story's times leave in doubt. */
export function BrokenBy({ items }: { items: StoryRule['broken_by'] }) {
  const sure = items.filter((item) => item.certain !== false)
  const unsure = items.filter((item) => item.certain === false)
  const named = (list: typeof items) =>
    list.map((item) => (item.when ? `${item.name} (${item.when})` : item.name)).join(', ')
  if (items.length === 0) return <>Your story keeps this rule as it stands.</>
  return (
    <>
      {sure.length > 0 && <>Right now it does not hold for: {named(sure)}.</>}
      {sure.length > 0 && unsure.length > 0 && ' '}
      {unsure.length > 0 && (
        <>
          It may not hold for: {named(unsure)}. The story's times are too loose to be sure, so this
          only ever warns.
        </>
      )}
    </>
  )
}

/**
 * A new rule, or a change to one. The author says it in a sentence and the
 * assistant drafts it, or builds it by hand; either way it is shown as it will
 * be checked, with examples, before it is kept.
 */
export function RuleDialog({
  rule,
  entries,
  onClose,
}: {
  rule?: StoryRule
  entries: CanonEntity[]
  onClose: () => void
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const predicates = (vocabulary.data?.predicates ?? []).map((item) => item.predicate)
  // Kinds of fact that hold a number, for a range.
  const numeric = (vocabulary.data?.predicates ?? [])
    .filter((item) => ['integer', 'decimal', 'quantity'].includes(item.object_shape))
    .map((item) => item.predicate)
  const types = (vocabulary.data?.entity_types ?? []).map((item) => item.name)

  const [sentence, setSentence] = useState(rule?.sentence ?? '')
  const [title, setTitle] = useState(rule?.title ?? '')
  const [body, setBody] = useState<RuleBody | null>(rule?.body ?? null)
  const [examples, setExamples] = useState<RuleExample[]>(rule?.examples ?? [])
  const [cannot, setCannot] = useState('')
  const [understood, setUnderstood] = useState('')

  const draft = useMutation({
    mutationFn: () => api.draftRule(scope, sentence.trim()),
    onSuccess: (found) => {
      setCannot(found.cannot_express)
      setUnderstood(found.understood)
      setBody(found.body ?? null)
      setExamples(found.examples)
      if (!title.trim()) setTitle(found.title)
    },
  })
  const check = useQuery({
    queryKey: scopedKey(scope, 'rule-check', JSON.stringify(body), JSON.stringify(examples)),
    queryFn: () => api.checkRule(scope, { body: body as RuleBody, examples }),
    enabled: body !== null,
  })
  const save = useMutation({
    mutationFn: () =>
      rule
        ? api.changeRule(scope, rule.id, { title: title.trim(), sentence: sentence.trim(), body, examples })
        : api.addRule(scope, { title: title.trim(), sentence: sentence.trim(), body: body as RuleBody, examples }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'rules') })
      onClose()
    },
  })

  const found: RuleCheck | undefined = check.data
  const disagree = (found?.example_results ?? []).filter((item) => !item.agrees)
  const ready =
    body !== null && title.trim().length > 0 && found !== undefined && found.problems.length === 0 && disagree.length === 0

  const byHand = () => {
    setCannot('')
    setBody(body ?? startBody(predicates[0] ?? ''))
  }
  /** Changing the rule by hand drops examples written for the earlier wording. */
  const edit = (next: RuleBody) => {
    setBody(next)
    setExamples([])
  }

  return (
    <Dialog title={rule ? 'Change this rule' : 'New story rule'} onClose={onClose} wide>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (ready) save.mutate()
        }}
      >
        <Field
          label="The rule, in your own words"
          hint="For example: “Every guild needs at least one member”, or “The ring has only one owner”."
        >
          {(props) => (
            <textarea {...props} rows={2} value={sentence} maxLength={600} onChange={(event) => setSentence(event.target.value)} />
          )}
        </Field>
        <div className="actions">
          <Button
            icon="spark"
            variant={body ? 'default' : 'primary'}
            disabled={!sentence.trim() || draft.isPending}
            onClick={() => draft.mutate()}
          >
            {draft.isPending ? 'Drafting…' : body ? 'Draft it again' : 'Draft it with the assistant'}
          </Button>
          {!body && (
            <Button disabled={vocabulary.isPending} onClick={byHand}>
              Build it by hand
            </Button>
          )}
        </div>
        {draft.isPending && <Loading>Working out how to check that…</Loading>}
        <ErrorNotice error={draft.error} prefix="Could not draft the rule" />
        {cannot && (
          <p className="notice plain" role="note">
            <span>
              {body ? (
                <>
                  <strong>Part of this could not be kept in the rule.</strong> {cannot} The rest is below.
                </>
              ) : (
                <>
                  <strong>This cannot be checked as a rule yet.</strong> {cannot} You can still build a
                  simpler rule by hand.
                </>
              )}
            </span>
          </p>
        )}

        {body && (
          <>
            {understood && <p className="muted">As the assistant understood it: {understood}</p>}
            <RuleBuilder
              body={body}
              types={types}
              predicates={predicates}
              numeric={numeric}
              entries={entries}
              onChange={edit}
            />

            <section className="card quiet stack tight" aria-label="How it will be checked">
              {check.isPending && <Loading>Checking…</Loading>}
              <ErrorNotice error={check.error} prefix="Could not check the rule" />
              {found && (
                <>
                  <p>
                    <strong>Checked as:</strong> {found.reading}
                  </p>
                  {found.problems.map((problem) => (
                    <p key={problem} className="notice danger" role="alert">
                      {problem}
                    </p>
                  ))}
                  {found.example_results.length > 0 && (
                    <ul className="rule-examples" aria-label="Examples">
                      {found.example_results.map((item, index) => (
                        <li key={index} className={item.agrees ? '' : 'disagrees'}>
                          {item.agrees
                            ? item.outcome === 'caught'
                              ? 'Would warn about: '
                              : 'Would allow: '
                            : `Should be ${item.expect}, but the rule would leave it ${item.outcome}: `}
                          {item.description}
                        </li>
                      ))}
                    </ul>
                  )}
                  {disagree.length > 0 && (
                    <p className="notice danger" role="alert">
                      The rule does not do what its examples say. Change it above, or draft it again.
                    </p>
                  )}
                  {found.problems.length === 0 && (
                    <p className="muted small">
                      <BrokenBy items={found.broken_by} />
                    </p>
                  )}
                </>
              )}
            </section>

            <Field label="A short name for it">
              {(props) => <input {...props} value={title} maxLength={160} onChange={(event) => setTitle(event.target.value)} />}
            </Field>
          </>
        )}

        <ErrorNotice error={save.error} prefix="Could not keep the rule" />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
            {save.isPending ? 'Saving…' : rule ? 'Save the rule' : 'Keep this rule'}
          </Button>
        </div>
        <p className="muted small">
          {rule?.enforcement === 'blocking'
            ? 'This rule blocks a change that would break it. Changing its words does not change that.'
            : 'A rule warns you when a change would break it. To have it block such a change instead, choose “Block the change” on the list of rules.'}
        </p>
      </form>
    </Dialog>
  )
}

function RuleBuilder({
  body,
  types,
  predicates,
  numeric,
  entries,
  onChange,
}: {
  body: RuleBody
  types: string[]
  predicates: string[]
  numeric: string[]
  entries: CanonEntity[]
  onChange: (body: RuleBody) => void
}) {
  const about = body.applies_to ?? {}
  const check = body.checks[0]
  const only = about.having?.[0]
  const active = entries.filter((entry) => entry.lifecycle === 'active')
  const first = predicates[0] ?? ''
  const setAbout = (changes: Partial<NonNullable<RuleBody['applies_to']>>) =>
    onChange({ ...body, applies_to: { ...about, ...changes } })
  const setCheck = (next: RuleCheckShape) => onChange({ ...body, checks: [next, ...body.checks.slice(1)] })
  const extra = body.checks.length - 1 + Math.max((about.having?.length ?? 0) - 1, 0)
  const patternProps = { types, predicates, entries: active }

  return (
    <fieldset className="rule-builder stack tight">
      <legend>The rule</legend>
      <div className="rule-line">
        <span>About</span>
        <label className="sr-only" htmlFor="rule-about">
          Which entries the rule is about
        </label>
        <select
          id="rule-about"
          value={about.entity_id ? `one:${about.entity_id}` : `type:${about.type ?? ''}`}
          onChange={(event) => {
            const [kind, value] = splitChoice(event.target.value)
            setAbout(kind === 'one' ? { entity_id: value, type: null } : { entity_id: null, type: value || null })
          }}
        >
          <option value="type:">every entry</option>
          <optgroup label="Every…">
            {types.map((type) => (
              <option key={type} value={`type:${type}`}>
                every {typeLabel(type).toLowerCase()}
              </option>
            ))}
          </optgroup>
          <optgroup label="Only one entry">
            {active.map((entry) => (
              <option key={entry.id} value={`one:${entry.id}`}>
                {entry.name}
              </option>
            ))}
          </optgroup>
        </select>
        <label className="check small">
          <input
            type="checkbox"
            checked={Boolean(only)}
            onChange={(event) => setAbout({ having: event.target.checked ? [emptyPattern(first)] : [] })}
          />
          only those that…
        </label>
      </div>
      {only && (
        <PatternFields
          label="Only those that"
          pattern={only}
          {...patternProps}
          onChange={(pattern) => setAbout({ having: [pattern, ...(about.having ?? []).slice(1)] })}
        />
      )}
      {!about.entity_id && (
        <ExceptFor body={body} entries={active} predicates={predicates} types={types} onChange={onChange} />
      )}

      <div className="rule-line">
        <label className="sr-only" htmlFor="rule-kind">
          Must or must not
        </label>
        <select
          id="rule-kind"
          value={check.kind}
          onChange={(event) => {
            const next = checkFor(event.target.value as CheckKind, check, first)
            // A range starts on a kind of fact that holds a number, when there is one.
            if (next.kind === 'between' && numeric.length > 0 && !numeric.includes(next.pattern.predicate)) {
              next.pattern = { predicate: numeric[0], direction: 'out' }
            }
            setCheck(next)
          }}
        >
          {KIND_GROUPS.map((group) => (
            <optgroup key={group} label={group}>
              {KINDS.filter((kind) => kind.group === group).map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {(check.kind === 'at_least' || check.kind === 'at_most') && (
          <>
            <label className="sr-only" htmlFor="rule-count">
              How many
            </label>
            <input
              id="rule-count"
              type="number"
              min={0}
              max={1000}
              className="rule-count"
              value={check.count ?? 1}
              onChange={(event) => setCheck({ ...check, count: Math.max(0, Number(event.target.value) || 0) })}
            />
            <span>of</span>
          </>
        )}
      </div>
      <CheckParts check={check} active={active} numeric={numeric} patternProps={patternProps} onChange={setCheck} />

      <div className="rule-line">
        <label htmlFor="rule-time">It must hold</label>
        <select
          id="rule-time"
          aria-describedby="rule-time-hint"
          value={body.time ?? 'as_recorded'}
          onChange={(event) => onChange({ ...body, time: event.target.value as RuleTime })}
        >
          {TIMES.map((time) => (
            <option key={time.value} value={time.value}>
              {time.label}
            </option>
          ))}
        </select>
      </div>
      <p id="rule-time-hint" className="hint">
        {TIMES.find((time) => time.value === (body.time ?? 'as_recorded'))?.says}
      </p>
      {extra > 0 && (
        <p className="muted small">
          This rule has {extra} more {extra === 1 ? 'condition' : 'conditions'}, kept as drafted. They show in
          “Checked as” below.
        </p>
      )}
    </fieldset>
  )
}

/** What the chosen kind of check needs: one fact, two facts, a range, or a belief. */
function CheckParts({
  check,
  active,
  numeric,
  patternProps,
  onChange,
}: {
  check: RuleCheckShape
  active: CanonEntity[]
  numeric: string[]
  patternProps: { types: string[]; predicates: string[]; entries: CanonEntity[] }
  onChange: (check: RuleCheckShape) => void
}) {
  const { predicates } = patternProps
  if (check.kind === 'between') {
    const offered = numeric.length > 0 ? numeric : predicates
    const known = offered.includes(check.pattern.predicate) ? offered : [check.pattern.predicate, ...offered]
    return (
      <div className="rule-line pattern" role="group" aria-label="The range">
        <select
          aria-label="Which value"
          value={check.pattern.predicate}
          onChange={(event) => onChange({ ...check, pattern: { predicate: event.target.value, direction: 'out' } })}
        >
          {known.filter(Boolean).map((predicate) => (
            <option key={predicate} value={predicate}>
              {predicateLabel(predicate)}
            </option>
          ))}
        </select>
        <label htmlFor="rule-low">from</label>
        <input
          id="rule-low"
          type="number"
          className="rule-count"
          value={check.low ?? ''}
          onChange={(event) => onChange({ ...check, low: rangeNumber(event.target.value) })}
        />
        <label htmlFor="rule-high">to</label>
        <input
          id="rule-high"
          type="number"
          className="rule-count"
          value={check.high ?? ''}
          onChange={(event) => onChange({ ...check, high: rangeNumber(event.target.value) })}
        />
        <span className="muted small">
          Leave one empty for no limit on that side.
          {numeric.length === 0 && ' None of your kinds of fact holds a number yet, so any value will be read as one.'}
        </span>
      </div>
    )
  }
  if (check.kind === 'implies' && check.if_has) {
    return (
      <>
        <PatternFields
          label="If it has"
          lead="If"
          pattern={check.if_has}
          {...patternProps}
          onChange={(if_has) => onChange({ ...check, if_has })}
        />
        <PatternFields
          label="It must also have"
          lead="then also"
          pattern={check.pattern}
          {...patternProps}
          onChange={(pattern) => onChange({ ...check, pattern })}
        />
      </>
    )
  }
  if (check.kind === 'not_both' && check.not_with) {
    return (
      <>
        <PatternFields
          label="It cannot have this"
          lead="This"
          pattern={check.pattern}
          {...patternProps}
          onChange={(pattern) => onChange({ ...check, pattern })}
        />
        <PatternFields
          label="Together with this"
          lead="together with"
          pattern={check.not_with}
          {...patternProps}
          onChange={(not_with) => onChange({ ...check, not_with })}
        />
      </>
    )
  }
  if (check.kind === 'believes' || check.kind === 'does_not_believe') {
    return (
      <>
        <div className="rule-line">
          <label htmlFor="rule-about-belief">that</label>
          <select
            id="rule-about-belief"
            value={check.about_id ?? ''}
            onChange={(event) => onChange({ ...check, about_id: event.target.value || null })}
          >
            <option value="">it itself</option>
            <optgroup label="Another entry">
              {active.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </optgroup>
          </select>
        </div>
        <PatternFields
          label="What it believes"
          pattern={check.pattern}
          {...patternProps}
          self={check.about_id ? (active.find((entry) => entry.id === check.about_id)?.name ?? 'that entry') : 'it'}
          onChange={(pattern) => onChange({ ...check, pattern })}
        />
      </>
    )
  }
  return (
    <PatternFields
      label="What must hold"
      pattern={check.pattern}
      {...patternProps}
      onChange={(pattern) => onChange({ ...check, pattern })}
    />
  )
}

/** Entries the rule leaves out: by name, by tag, or because of a fact they have. */
function ExceptFor({
  body,
  entries,
  types,
  predicates,
  onChange,
}: {
  body: RuleBody
  entries: CanonEntity[]
  types: string[]
  predicates: string[]
  onChange: (body: RuleBody) => void
}) {
  const except = body.except_for ?? {}
  const named = except.entity_ids ?? []
  const fact = except.having?.[0]
  const [open, setOpen] = useState(named.length > 0 || Boolean(except.tag) || Boolean(fact))
  const set = (changes: Partial<NonNullable<RuleBody['except_for']>>) =>
    onChange({ ...body, except_for: { ...except, ...changes } })
  const name = (id: string) => entries.find((entry) => entry.id === id)?.name ?? 'An entry no longer there'
  const left = entries.filter((entry) => !named.includes(entry.id))
  return (
    <>
      <label className="check small">
        <input
          type="checkbox"
          checked={open}
          onChange={(event) => {
            setOpen(event.target.checked)
            if (!event.target.checked) {
              const { except_for: _dropped, ...rest } = body
              onChange(rest as RuleBody)
            }
          }}
        />
        except some entries…
      </label>
      {open && (
        <div className="rule-except stack tight" role="group" aria-label="Except">
          {named.length > 0 && (
            <ul className="chips" aria-label="Left out">
              {named.map((id) => (
                <li key={id} className="chip">
                  {name(id)}
                  <button
                    type="button"
                    className="chip-remove"
                    aria-label={`Stop leaving out ${name(id)}`}
                    onClick={() => set({ entity_ids: named.filter((other) => other !== id) })}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="rule-line">
            <label htmlFor="rule-except-entry">Leave out</label>
            <select
              id="rule-except-entry"
              value=""
              disabled={left.length === 0}
              onChange={(event) => event.target.value && set({ entity_ids: [...named, event.target.value] })}
            >
              <option value="">{left.length === 0 ? 'Nothing more to leave out' : 'Choose an entry…'}</option>
              {left.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </select>
            <label htmlFor="rule-except-tag">and anything tagged</label>
            <input
              id="rule-except-tag"
              value={except.tag ?? ''}
              maxLength={60}
              placeholder="a tag"
              onChange={(event) => set({ tag: event.target.value || null })}
            />
          </div>
          <label className="check small">
            <input
              type="checkbox"
              checked={Boolean(fact)}
              onChange={(event) => set({ having: event.target.checked ? [emptyPattern(predicates[0] ?? '')] : [] })}
            />
            and anything that…
          </label>
          {fact && (
            <PatternFields
              label="Leave out anything that"
              pattern={fact}
              types={types}
              predicates={predicates}
              entries={entries}
              onChange={(pattern) => set({ having: [pattern, ...(except.having ?? []).slice(1)] })}
            />
          )}
        </div>
      )}
    </>
  )
}

export function PatternFields({
  label,
  pattern,
  types,
  predicates,
  entries,
  self = 'it',
  lead,
  onChange,
}: {
  label: string
  pattern: RulePattern
  types: string[]
  predicates: string[]
  entries: CanonEntity[]
  /** What "it" is: the entry being checked, or the one a belief is about. */
  self?: string
  /** Words shown before the fact, where a rule has more than one ("if it has"). */
  lead?: string
  onChange: (pattern: RulePattern) => void
}) {
  const other = pattern.other_id ? `one:${pattern.other_id}` : `type:${pattern.other_type ?? ''}`
  const known = predicates.includes(pattern.predicate) ? predicates : [pattern.predicate, ...predicates]
  return (
    <div className="rule-line pattern" role="group" aria-label={label}>
      {lead && (
        <span className="rule-lead" aria-hidden="true">
          {lead}
        </span>
      )}
      <select
        aria-label={`${label}: which way round`}
        value={pattern.direction ?? 'out'}
        onChange={(event) => onChange({ ...pattern, direction: event.target.value as 'out' | 'in' })}
      >
        <option value="out">{self}</option>
        <option value="in">something else</option>
      </select>
      <select
        aria-label={`${label}: kind of fact`}
        value={pattern.predicate}
        onChange={(event) => onChange({ ...pattern, predicate: event.target.value })}
      >
        {known.filter(Boolean).map((predicate) => (
          <option key={predicate} value={predicate}>
            {predicateLabel(predicate)}
          </option>
        ))}
      </select>
      {pattern.direction === 'in' ? (
        <span>{self}, where that is</span>
      ) : null}
      <select
        aria-label={`${label}: the other entry`}
        value={other}
        onChange={(event) => {
          const [kind, value] = splitChoice(event.target.value)
          onChange({
            ...pattern,
            other_id: kind === 'one' ? value : null,
            other_type: kind === 'type' && value ? value : null,
          })
        }}
      >
        <option value="type:">anything</option>
        <optgroup label="Any…">
          {types.map((type) => (
            <option key={type} value={`type:${type}`}>
              any {typeLabel(type).toLowerCase()}
            </option>
          ))}
        </optgroup>
        <optgroup label="One entry">
          {entries.map((entry) => (
            <option key={entry.id} value={`one:${entry.id}`}>
              {entry.name}
            </option>
          ))}
        </optgroup>
      </select>
    </div>
  )
}
