import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CanonEntity, type PlanItem, type SceneCondition } from '../api/client'
import { emptyPattern, KINDS, type CheckKind } from '../lib/rules'
import { scopedKey, useCanonWrite, useProject } from '../project'
import { PatternFields } from './RuleDialog'
import { Button, Dialog, WriteProblem } from './ui'

const MAX_CONDITIONS = 6

/**
 * What must be true before a scene, and what it should leave true afterwards.
 * Each is about one entry and is checked against the story the same way a story
 * rule is. They show whether the story is ready for the scene; they stop nothing.
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
  const vocabulary = useQuery({
    queryKey: scopedKey(scope, 'vocabulary'),
    queryFn: () => api.vocabulary(scope),
  })
  const predicates = (vocabulary.data?.predicates ?? []).map((item) => item.predicate)
  const types = (vocabulary.data?.entity_types ?? []).map((item) => item.name)
  const active = entries.filter((entry) => entry.lifecycle === 'active')
  const [rows, setRows] = useState<SceneCondition[]>(scene.conditions)

  const save = useCanonWrite(
    (expectedRevision) =>
      api.setSceneConditions(scope, scene.id, { conditions: rows, expected_revision: expectedRevision }),
    onClose,
  )
  const change = (index: number, next: SceneCondition) =>
    setRows(rows.map((row, at) => (at === index ? next : row)))
  const add = (when: 'before' | 'after') =>
    setRows([
      ...rows,
      {
        when,
        entity_id: active[0]?.id ?? '',
        check: { kind: 'has', count: 1, pattern: emptyPattern(predicates[0] ?? '') },
      },
    ])
  const full = rows.length >= MAX_CONDITIONS
  const unchanged = JSON.stringify(rows) === JSON.stringify(scene.conditions)

  const group = (when: 'before' | 'after', heading: string, hint: string) => (
    <section className="stack tight" aria-label={heading}>
      <div>
        <h3>{heading}</h3>
        <p className="muted small">{hint}</p>
      </div>
      {rows.map((row, index) =>
        row.when !== when ? null : (
          <fieldset className="rule-builder stack tight" key={index}>
            <legend className="sr-only">
              {heading}, condition {index + 1}
            </legend>
            <div className="rule-line">
              <select
                aria-label="Which entry"
                value={row.entity_id}
                onChange={(event) => change(index, { ...row, entity_id: event.target.value })}
              >
                {active.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
              <select
                aria-label="Must or must not"
                value={row.check.kind}
                onChange={(event) =>
                  change(index, { ...row, check: { ...row.check, kind: event.target.value as CheckKind } })
                }
              >
                {KINDS.filter((kind) => kind.value === 'has' || kind.value === 'lacks').map((kind) => (
                  <option key={kind.value} value={kind.value}>
                    {kind.label}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="ghost" onClick={() => setRows(rows.filter((_, at) => at !== index))}>
                Remove
              </Button>
            </div>
            <PatternFields
              label="What must be true"
              pattern={row.check.pattern}
              types={types}
              predicates={predicates}
              entries={active}
              onChange={(pattern) => change(index, { ...row, check: { ...row.check, pattern } })}
            />
          </fieldset>
        ),
      )}
      <div className="actions">
        <Button size="sm" icon="plus" disabled={full || active.length === 0 || vocabulary.isPending} onClick={() => add(when)}>
          {when === 'before' ? 'Add something that must be true first' : 'Add something it should change'}
        </Button>
      </div>
    </section>
  )

  return (
    <Dialog title={`Before and after “${scene.title}”`} onClose={onClose} wide>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void save.submit()
        }}
      >
        {active.length === 0 && (
          <p className="notice plain" role="note">
            These are about entries in your story bible, and there are none yet.
          </p>
        )}
        {group('before', 'Before this scene', 'What has to be true in your story for the scene to make sense.')}
        {group('after', 'After this scene', 'What the scene is meant to leave true once it has happened.')}
        <WriteProblem write={save} scope={scope} />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={unchanged || save.pending}>
            {save.pending ? 'Saving…' : 'Save'}
          </Button>
        </div>
        <p className="muted small">These show whether your story is ready for the scene. They never stop you.</p>
      </form>
    </Dialog>
  )
}
