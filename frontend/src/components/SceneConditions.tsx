import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type Fact, type PlanItem, type SceneCondition } from '../api/client'
import { factSentence } from '../lib/facts'
import { emptyPattern, KINDS, type CheckKind } from '../lib/rules'
import { scopedKey, useCanonWrite, useEntries, useProject } from '../project'
import { PatternFields } from './RuleDialog'
import { Button, Dialog, WriteProblem } from './ui'

const MAX_CONDITIONS = 6
const MAX_WORDS = 300

type When = SceneCondition['when']
type Strength = SceneCondition['strength']

/** A condition about one entry, or one in the author's own words (never both). */
function structured(when: When, strength: Strength, entityId: string, predicate: string): SceneCondition {
  return { when, strength, entity_id: entityId, check: { kind: 'has', count: 1, pattern: emptyPattern(predicate) } }
}

function inWords(when: When, strength: Strength, words = ''): SceneCondition {
  return { when, strength, words }
}

/** A fact about an entry as the pattern a condition checks: the same kind, the same other end. */
function patternOf(fact: Fact) {
  const object = fact.object as { entity_id?: string; value?: unknown }
  return object.entity_id
    ? { predicate: fact.predicate, direction: 'out' as const, other_id: object.entity_id }
    : { predicate: fact.predicate, direction: 'out' as const, value: String(object.value ?? '') }
}

/**
 * What must be true before a scene, and what it should leave true afterwards.
 * Each is required or only preferred, and is either about one entry (checked against
 * the story the same way a story rule is, at the scene's point in story time) or
 * written in the author's own words (given to the assistant, never checked).
 * They show whether the story is ready for the scene; they stop nothing.
 */
export function SceneConditionsDialog({
  scene,
  entries,
  onClose,
}: {
  scene: PlanItem
  entries: CanonEntity[]
  onClose: () => void
}) {
  const { scope } = useProject()
  const named = useEntries(scope)
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const facts = useQuery({
    queryKey: scopedKey(scope, 'facts', 'all'),
    queryFn: () => api.listFacts(scope, { limit: 200 }),
  })
  const predicates = (vocabulary.data?.predicates ?? []).map((item) => item.predicate)
  const types = (vocabulary.data?.entity_types ?? []).map((item) => item.name)
  const active = entries.filter((entry) => entry.lifecycle === 'active')
  const [rows, setRows] = useState<SceneCondition[]>(scene.conditions)
  const [tried, setTried] = useState(false)

  const save = useCanonWrite(
    (expectedRevision) =>
      api.setSceneConditions(scope, scene.id, {
        conditions: rows.map((row) =>
          row.words !== undefined && row.words !== null
            ? { when: row.when, strength: row.strength, words: row.words.trim() }
            : row,
        ),
        expected_revision: expectedRevision,
      }),
    onClose,
  )
  const change = (index: number, next: SceneCondition) => setRows(rows.map((row, at) => (at === index ? next : row)))
  const add = (when: When) =>
    setRows([
      ...rows,
      active.length > 0 ? structured(when, 'hard', active[0].id, predicates[0] ?? '') : inWords(when, 'hard'),
    ])
  const blank = (row: SceneCondition) => row.words !== undefined && row.words !== null && !row.words.trim()
  const full = rows.length >= MAX_CONDITIONS
  const unchanged = JSON.stringify(rows) === JSON.stringify(scene.conditions)

  const group = (when: When, heading: string, hint: string) => (
    <section className="stack tight" aria-label={heading}>
      <div>
        <h3>{heading}</h3>
        <p className="muted small">{hint}</p>
      </div>
      {rows.map((row, index) => {
        if (row.when !== when) return null
        const wordy = row.words !== undefined && row.words !== null
        const about = (facts.data?.items ?? []).filter((fact) => fact.subject_id === row.entity_id)
        const number = rows.slice(0, index + 1).filter((other) => other.when === when).length
        const name = `${heading}, condition ${number}`
        return (
          <fieldset className="rule-builder stack tight" key={index}>
            <legend className="sr-only">{name}</legend>
            <div className="rule-line">
              <select
                aria-label={`How much it matters (${name})`}
                value={row.strength}
                onChange={(event) => change(index, { ...row, strength: event.target.value as Strength })}
              >
                <option value="hard">Required</option>
                <option value="soft">Preferred</option>
              </select>
              <select
                aria-label={`How it is said (${name})`}
                value={wordy ? 'words' : 'entry'}
                onChange={(event) =>
                  change(
                    index,
                    event.target.value === 'words'
                      ? inWords(when, row.strength)
                      : structured(when, row.strength, active[0]?.id ?? '', predicates[0] ?? ''),
                  )
                }
              >
                <option value="entry" disabled={active.length === 0}>
                  About an entry
                </option>
                <option value="words">In my own words</option>
              </select>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Remove ${name}`}
                onClick={() => setRows(rows.filter((_, at) => at !== index))}
              >
                Remove
              </Button>
            </div>
            {wordy ? (
              <div className="stack tight">
                <label className="field">
                  <span>What must be true, in your words</span>
                  <input
                    value={row.words ?? ''}
                    maxLength={MAX_WORDS}
                    aria-invalid={tried && blank(row) ? true : undefined}
                    aria-describedby={`words-${index}-hint${tried && blank(row) ? ` words-${index}-problem` : ''}`}
                    placeholder="The storm has broken"
                    onChange={(event) => change(index, { ...row, words: event.target.value })}
                  />
                </label>
                {tried && blank(row) && (
                  <span className="error-text" id={`words-${index}-problem`}>
                    Write what must be true, or remove this one.
                  </span>
                )}
                <span className="muted small" id={`words-${index}-hint`}>
                  Given to the assistant with the scene; it is not checked.
                </span>
              </div>
            ) : (
              <>
                <div className="rule-line">
                  <select
                    aria-label={`Which entry (${name})`}
                    value={row.entity_id ?? ''}
                    onChange={(event) => change(index, { ...row, entity_id: event.target.value })}
                  >
                    {active.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.name}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label={`Must or must not (${name})`}
                    value={row.check?.kind ?? 'has'}
                    onChange={(event) =>
                      row.check && change(index, { ...row, check: { ...row.check, kind: event.target.value as CheckKind } })
                    }
                  >
                    {KINDS.filter((kind) => kind.value === 'has' || kind.value === 'lacks').map((kind) => (
                      <option key={kind.value} value={kind.value}>
                        {kind.label}
                      </option>
                    ))}
                  </select>
                </div>
                {about.length > 0 && (
                  <label className="field">
                    <span>Start from a fact in your story bible</span>
                    <select
                      value=""
                      onChange={(event) => {
                        const fact = about.find((item) => item.id === event.target.value)
                        if (fact && row.check) change(index, { ...row, check: { ...row.check, pattern: patternOf(fact) } })
                      }}
                    >
                      <option value="">Choose a fact to copy…</option>
                      {about.map((fact) => (
                        <option key={fact.id} value={fact.id}>
                          {factSentence(fact, named.names)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {row.check && (
                  <PatternFields
                    label="What must be true"
                    pattern={row.check.pattern}
                    types={types}
                    predicates={predicates}
                    entries={active}
                    onChange={(pattern) => row.check && change(index, { ...row, check: { ...row.check, pattern } })}
                  />
                )}
              </>
            )}
          </fieldset>
        )
      })}
      <div className="actions">
        <Button size="sm" icon="plus" disabled={full || vocabulary.isPending} onClick={() => add(when)}>
          {when === 'before' ? 'Add something that must be true first' : 'Add something it should change'}
        </Button>
      </div>
    </section>
  )

  return (
    <Dialog title={`Before and after “${scene.title}”`} onClose={onClose} wide>
      <form
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          setTried(true)
          if (rows.some(blank)) return
          void save.submit()
        }}
      >
        {active.length === 0 && (
          <p className="notice plain" role="note">
            There are no entries in your story bible yet, so write these in your own words.
          </p>
        )}
        {group(
          'before',
          'Before this scene',
          'What has to be true for the scene to make sense. Checked at the scene’s point in story time, if it has one, and against what earlier scenes are meant to change.',
        )}
        {group('after', 'After this scene', 'What the scene is meant to leave true once it has happened.')}
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={unchanged || save.pending}>
            {save.pending ? 'Saving…' : 'Save'}
          </Button>
        </div>
        <p className="muted small">
          A required one that is not true where the scene comes is listed for you to look at; a preferred one is a
          note. Neither ever stops you.
        </p>
      </form>
    </Dialog>
  )
}
