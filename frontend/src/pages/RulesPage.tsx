import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type StoryRule } from '../api/client'
import { RuleDialog } from '../components/RuleDialog'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorNotice, Loading, ReadOnlyNotice } from '../components/ui'
import { entryPath, scopedKey, useEntries, useProject } from '../project'

/**
 * The author's own rules for the story: things that must stay true, said in a
 * sentence and checked without a model. A rule warns when a change would break
 * it; the author can choose to have it block such a change instead (CON-015).
 */
export function RulesPage() {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const rules = useQuery({ queryKey: scopedKey(scope, 'rules'), queryFn: () => api.rules(scope) })
  const [editing, setEditing] = useState<{ rule?: StoryRule } | null>(null)
  const [removing, setRemoving] = useState<StoryRule | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: scopedKey(scope, 'rules') })

  const pause = useMutation({
    mutationFn: (rule: StoryRule) =>
      api.changeRule(scope, rule.id, { status: rule.status === 'on' ? 'paused' : 'on' }),
    onSuccess: refresh,
  })
  // Warn or block: reversible, so it happens at once and says what it now does.
  const [enforced, setEnforced] = useState<StoryRule | null>(null)
  const enforce = useMutation({
    mutationFn: ({ rule, enforcement }: { rule: StoryRule; enforcement: StoryRule['enforcement'] }) =>
      api.changeRule(scope, rule.id, { enforcement }),
    onSuccess: async (changed) => {
      setEnforced(changed)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (rule: StoryRule) => api.removeRule(scope, rule.id),
    onSuccess: async () => {
      await refresh()
      setRemoving(null)
    },
  })
  const items = rules.data?.items ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Story rules</h1>
          <p className="lede">
            Things that must stay true in your story, said in a sentence. A rule warns you when a
            change would break it, or, if you choose, holds the change back until it is fixed.
          </p>
        </div>
        {canWrite && (
          <div className="actions">
            <Button variant="primary" icon="plus" onClick={() => setEditing({})}>
              New rule
            </Button>
          </div>
        )}
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {rules.isPending && <Loading>Opening your rules…</Loading>}
        <ErrorNotice error={rules.error} prefix="Could not open your rules" />
        <ErrorNotice error={pause.error} prefix="Could not change the rule" />
        {enforced && (
          <div className="notice ok" role="status">
            <p>
              <strong>“{enforced.title}”</strong>{' '}
              {enforced.enforcement === 'blocking'
                ? 'now blocks any change that would break it.'
                : 'now warns you, and lets you add a change that breaks it.'}
            </p>
            <Button size="sm" variant="ghost" onClick={() => setEnforced(null)}>
              Dismiss
            </Button>
          </div>
        )}

        {rules.isSuccess && items.length === 0 && (
          <EmptyState
            icon="shield"
            title="No rules yet"
            action={canWrite ? <Button onClick={() => setEditing({})}>Write your first rule</Button> : undefined}
          >
            You do not need any. If there is something your story must never get wrong, such as
            “the ring has only one owner”, write it here and you will be told when a change would
            break it.
          </EmptyState>
        )}

        {items.length > 0 && (
          <ul className="rule-list">
            {items.map((rule) => (
              <li key={rule.id} className="card stack tight">
                <div className="actions" style={{ justifyContent: 'space-between' }}>
                  <h2>{rule.title}</h2>
                  <Standing rule={rule} />
                </div>
                {rule.sentence && <p>“{rule.sentence}”</p>}
                <p className="muted">Checked as: {rule.reading}</p>
                {rule.status === 'on' && (
                  <Breaks
                    label="Does not hold for:"
                    items={rule.broken_by.filter((item) => item.certain !== false)}
                    scope={scope}
                  />
                )}
                {rule.status === 'on' && (
                  <Breaks
                    label="May not hold for:"
                    note="The story's times are too loose to be sure, so this only ever warns."
                    items={rule.broken_by.filter((item) => item.certain === false)}
                    scope={scope}
                  />
                )}
                <Enforcement
                  rule={rule}
                  canWrite={canWrite}
                  busy={enforce.isPending}
                  error={enforce.variables?.rule.id === rule.id ? enforce.error : null}
                  onChange={(enforcement) => {
                    setEnforced(null)
                    enforce.mutate({ rule, enforcement })
                  }}
                />
                {rule.example_results.length > 0 && (
                  <details className="small">
                    <summary>Examples</summary>
                    <ul className="rule-examples">
                      {rule.example_results.map((item, index) => (
                        <li key={index} className={item.agrees ? '' : 'disagrees'}>
                          {item.outcome === 'caught' ? 'Would warn about: ' : 'Would allow: '}
                          {item.description}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {canWrite && (
                  <div className="actions">
                    <Button size="sm" icon="edit" aria-label={`Change ${rule.title}`} onClick={() => setEditing({ rule })}>
                      Change
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pause.isPending}
                      aria-label={`${rule.status === 'on' ? 'Pause' : 'Turn on'} ${rule.title}`}
                      onClick={() => pause.mutate(rule)}
                    >
                      {rule.status === 'on' ? 'Pause' : 'Turn on'}
                    </Button>
                    <Button size="sm" variant="ghost" aria-label={`Remove ${rule.title}`} onClick={() => setRemoving(rule)}>
                      Remove
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {editing && (
        <RuleDialog
          key={editing.rule?.id ?? 'new'}
          rule={editing.rule}
          entries={entries.items}
          onClose={() => setEditing(null)}
        />
      )}
      {removing && (
        <ConfirmDialog
          title={`Remove the rule “${removing.title}”?`}
          confirmLabel="Remove the rule"
          keepLabel="Keep it"
          busy={remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        >
          You will no longer be warned about it. Your story is not changed. To stop the warnings for
          a while and keep the rule, pause it instead.
        </ConfirmDialog>
      )}
    </>
  )
}

/** Whether a rule holds now: broken, perhaps broken (times too loose to tell), holds, or paused. */
function Standing({ rule }: { rule: StoryRule }) {
  if (rule.status !== 'on') return <Badge tone="neutral">Paused</Badge>
  if (rule.broken_by.some((item) => item.certain !== false)) return <Badge tone="warn">Broken now</Badge>
  if (rule.broken_by.length > 0) return <Badge tone="warn">May be broken</Badge>
  return <Badge tone="ok">Holds</Badge>
}

/** The entries a rule does not hold for, each a link to its page, with when where it matters. */
function Breaks({
  label,
  note,
  items,
  scope,
}: {
  label: string
  note?: string
  items: StoryRule['broken_by']
  scope: Parameters<typeof entryPath>[0]
}) {
  if (items.length === 0) return null
  return (
    <p>
      {label}{' '}
      {items.map((item, index) => (
        <span key={item.entity_id}>
          {index > 0 && ', '}
          <Link to={entryPath(scope, item.entity_id)}>{item.name}</Link>
          {item.when && <span className="muted"> ({item.when})</span>}
        </span>
      ))}
      .{note && <span className="muted small"> {note}</span>}
    </p>
  )
}

const ENFORCEMENT: Record<StoryRule['enforcement'], { label: string; says: string }> = {
  warning: {
    label: 'Warn me',
    says: 'You are told when a change would break it, and you can still add the change.',
  },
  blocking: {
    label: 'Block the change',
    says:
      'A change that would break it cannot be added until you fix the change or set the rule back to warn. ' +
      'Where your story already breaks it, other changes are not held back.',
  },
}

/** What happens when a change would break the rule: a warning, or a block. */
function Enforcement({
  rule,
  canWrite,
  busy,
  error,
  onChange,
}: {
  rule: StoryRule
  canWrite: boolean
  busy: boolean
  error: unknown
  onChange: (enforcement: StoryRule['enforcement']) => void
}) {
  const current = ENFORCEMENT[rule.enforcement]
  const paused = rule.status !== 'on'
  if (!canWrite) {
    return (
      <p className="small">
        <strong>When a change would break it:</strong>{' '}
        {rule.enforcement === 'blocking' ? 'the change is blocked.' : 'you are warned.'}
      </p>
    )
  }
  return (
    <fieldset className="rule-enforcement stack tight">
      <legend className="label">When a change would break it</legend>
      <div className="segmented" role="group" aria-label={`When a change would break ${rule.title}`}>
        {(['warning', 'blocking'] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={rule.enforcement === value}
            disabled={busy}
            onClick={() => rule.enforcement !== value && onChange(value)}
          >
            {ENFORCEMENT[value].label}
          </button>
        ))}
      </div>
      <p className="hint">
        {current.says}
        {paused ? ' While the rule is paused it does neither.' : ''}
      </p>
      <ErrorNotice error={error} prefix="Could not change the rule" />
    </fieldset>
  )
}
