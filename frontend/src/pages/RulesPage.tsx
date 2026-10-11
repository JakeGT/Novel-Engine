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
 * it. It never stops anything.
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
            change would break it. It never stops you.
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
                  <Badge tone={rule.status === 'on' ? (rule.broken_by.length > 0 ? 'warn' : 'ok') : 'neutral'}>
                    {rule.status === 'on' ? (rule.broken_by.length > 0 ? 'Broken now' : 'Holds') : 'Paused'}
                  </Badge>
                </div>
                {rule.sentence && <p>“{rule.sentence}”</p>}
                <p className="muted">Checked as: {rule.reading}</p>
                {rule.status === 'on' && rule.broken_by.length > 0 && (
                  <p>
                    Does not hold for:{' '}
                    {rule.broken_by.map((item, index) => (
                      <span key={item.entity_id}>
                        {index > 0 && ', '}
                        <Link to={entryPath(scope, item.entity_id)}>{item.name}</Link>
                      </span>
                    ))}
                  </p>
                )}
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
