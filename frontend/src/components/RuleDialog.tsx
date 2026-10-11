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
import { emptyPattern, KINDS, splitChoice, type CheckKind } from '../lib/rules'
import { predicateLabel, typeLabel } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Field, Loading } from './ui'

function startBody(predicate: string): RuleBody {
  return { applies_to: {}, mode: 'all', checks: [{ kind: 'has', count: 1, pattern: emptyPattern(predicate) }] }
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
              <strong>This cannot be checked as a rule yet.</strong> {cannot} You can still build a
              simpler rule by hand.
            </span>
          </p>
        )}

        {body && (
          <>
            {understood && <p className="muted">As the assistant understood it: {understood}</p>}
            <RuleBuilder body={body} types={types} predicates={predicates} entries={entries} onChange={edit} />

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
                      {found.broken_by.length === 0
                        ? 'Your story keeps this rule as it stands.'
                        : `Right now it does not hold for: ${found.broken_by.map((item) => item.name).join(', ')}.`}
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
        <p className="muted small">A rule only ever warns you. It never stops a change.</p>
      </form>
    </Dialog>
  )
}

function RuleBuilder({
  body,
  types,
  predicates,
  entries,
  onChange,
}: {
  body: RuleBody
  types: string[]
  predicates: string[]
  entries: CanonEntity[]
  onChange: (body: RuleBody) => void
}) {
  const about = body.applies_to ?? {}
  const check = body.checks[0]
  const only = about.having?.[0]
  const active = entries.filter((entry) => entry.lifecycle === 'active')
  const setAbout = (changes: Partial<NonNullable<RuleBody['applies_to']>>) =>
    onChange({ ...body, applies_to: { ...about, ...changes } })
  const setCheck = (changes: Partial<RuleBody['checks'][number]>) =>
    onChange({ ...body, checks: [{ ...check, ...changes }, ...body.checks.slice(1)] })
  const extra = body.checks.length - 1 + Math.max((about.having?.length ?? 0) - 1, 0)

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
            onChange={(event) =>
              setAbout({ having: event.target.checked ? [emptyPattern(predicates[0] ?? '')] : [] })
            }
          />
          only those that…
        </label>
      </div>
      {only && (
        <PatternFields
          label="Only those that"
          pattern={only}
          types={types}
          predicates={predicates}
          entries={active}
          onChange={(pattern) => setAbout({ having: [pattern, ...(about.having ?? []).slice(1)] })}
        />
      )}
      <div className="rule-line">
        <label className="sr-only" htmlFor="rule-kind">
          Must or must not
        </label>
        <select id="rule-kind" value={check.kind} onChange={(event) => setCheck({ kind: event.target.value as CheckKind })}>
          {KINDS.map((kind) => (
            <option key={kind.value} value={kind.value}>
              {kind.label}
            </option>
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
              onChange={(event) => setCheck({ count: Math.max(0, Number(event.target.value) || 0) })}
            />
            <span>of</span>
          </>
        )}
      </div>
      <PatternFields
        label="What must hold"
        pattern={check.pattern}
        types={types}
        predicates={predicates}
        entries={active}
        onChange={(pattern) => setCheck({ pattern })}
      />
      {extra > 0 && (
        <p className="muted small">
          This rule has {extra} more {extra === 1 ? 'condition' : 'conditions'}, kept as drafted. They show in
          “Checked as” below.
        </p>
      )}
    </fieldset>
  )
}

export function PatternFields({
  label,
  pattern,
  types,
  predicates,
  entries,
  onChange,
}: {
  label: string
  pattern: RulePattern
  types: string[]
  predicates: string[]
  entries: CanonEntity[]
  onChange: (pattern: RulePattern) => void
}) {
  const other = pattern.other_id ? `one:${pattern.other_id}` : `type:${pattern.other_type ?? ''}`
  const known = predicates.includes(pattern.predicate) ? predicates : [pattern.predicate, ...predicates]
  return (
    <div className="rule-line pattern" role="group" aria-label={label}>
      <select
        aria-label={`${label}: which way round`}
        value={pattern.direction ?? 'out'}
        onChange={(event) => onChange({ ...pattern, direction: event.target.value as 'out' | 'in' })}
      >
        <option value="out">it</option>
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
        <span>it, where that is</span>
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
