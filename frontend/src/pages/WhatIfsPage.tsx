import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type WhatIf, type WhatIfChange } from '../api/client'
import { Badge, Button, EmptyState, ErrorNotice, Field, Loading, ReadOnlyNotice } from '../components/ui'
import { useWorkingWhatIf } from '../lib/whatif'
import { objectText, plural, predicateLabel } from '../lib/words'
import { scopedKey, useEntries, useInvalidateProject, useProject } from '../project'

const KIND_WORDS: Record<string, string> = {
  added: 'Added',
  changed: 'Changed',
  ended: 'No longer true',
  archived: 'Archived',
}

const RECORD_WORDS: Record<string, string> = {
  entity: 'Entry',
  fact: 'Fact',
  belief: 'Belief',
  event: 'Event',
  plan_item: 'Plan',
  asset_link: 'Picture',
}

type Parts = { subject_id: string; predicate: string; object: Record<string, unknown>; holder_id?: string }

/** A change's record in the author's words: facts and beliefs phrased as sentences. */
function changeText(label: string, parts: unknown, names: Map<string, string>): string {
  if (!parts || typeof parts !== 'object') return label
  const fact = parts as Parts
  const sentence = `${names.get(fact.subject_id) ?? 'An entry'} ${predicateLabel(fact.predicate)} ${objectText(fact.object, names)}`
  return fact.holder_id ? `${names.get(fact.holder_id) ?? 'Someone'} believes ${sentence}` : sentence
}

function ChangeList({ items, empty }: { items: WhatIfChange[]; empty: string }) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  if (items.length === 0) return <p className="muted">{empty}</p>
  return (
    <ul className="rows">
      {items.map((item) => (
        <li className="row" key={item.key}>
          <span className="grow">{changeText(item.label, item.parts, entries.names)}</span>
          <span className="change-meta">
            <span className="muted small">{RECORD_WORDS[item.record_type] ?? 'Record'}</span>
            <Badge>{KIND_WORDS[item.kind] ?? item.kind}</Badge>
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Every what-if of the story, and a way to start one. */
export function WhatIfsPage() {
  const { scope, canWrite } = useProject()
  const { whatIf: working, set: work } = useWorkingWhatIf(scope.projectId)
  const invalidate = useInvalidateProject(scope)
  const list = useQuery({ queryKey: scopedKey(scope, 'what-ifs'), queryFn: () => api.whatIfs(scope) })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () => api.startWhatIf(scope, { name: name.trim(), description: description.trim() || null }),
    onSuccess: async (made) => {
      setName('')
      setDescription('')
      work({ id: made.id, name: made.name })
      await invalidate()
    },
  })
  const aside = useMutation({
    mutationFn: (target: { item: WhatIf; aside: boolean }) => api.setWhatIfAside(scope, target.item.id, target.aside),
    onSuccess: async (_result, target) => {
      if (target.aside && working?.id === target.item.id) work(null)
      await invalidate()
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return setProblem('Give the what-if a name, such as “What if Mara refused?”.')
    setProblem(null)
    start.mutate()
  }

  const items = list.data?.items ?? []
  return (
    <>
      <div className="page-head">
        <div>
          <h1>What-ifs</h1>
          <p className="lede">
            Try a different turn of the story without touching it. A what-if starts as a copy of
            your story as it is now; compare the two and bring back what you want to keep.
          </p>
        </div>
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />

        {list.isPending && <Loading>Loading what-ifs…</Loading>}
        <ErrorNotice error={list.error} prefix="Could not load what-ifs" />
        <ErrorNotice error={aside.error} prefix="Could not change it" />
        {list.isSuccess && items.length === 0 && (
          <EmptyState icon="branch" title="No what-ifs yet">
            Nothing you do in a what-if changes your story, so it is a safe place to try out a
            different choice and see what it would change.
          </EmptyState>
        )}

        {items.length > 0 && (
          <section className="stack tight" aria-labelledby="list-heading">
            <h2 id="list-heading">Your what-ifs</h2>
            <ul className="rule-list">
              {items.map((item) => {
                const here = working?.id === item.id
                return (
                  <li key={item.id} className="card stack tight">
                    <div className="actions" style={{ justifyContent: 'space-between' }}>
                      <h3>{item.name}</h3>
                      {here && <Badge tone="ok">You are working here</Badge>}
                      {item.status === 'set_aside' && <Badge>Set aside</Badge>}
                    </div>
                    {item.description && <p>{item.description}</p>}
                    <p className="muted small">
                      Started from version {item.started_at_version} of your story ·{' '}
                      {plural(item.changes, 'change')} made in it since
                    </p>
                    <div className="actions">
                      {!here && item.status === 'active' && (
                        <Button size="sm" onClick={() => work({ id: item.id, name: item.name })}>
                          Work in this what-if
                        </Button>
                      )}
                      <Link className="btn sm" to={`/projects/${scope.projectId}/what-ifs/${item.id}`}>
                        Compare with the story
                      </Link>
                      {canWrite && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={aside.isPending}
                          onClick={() => aside.mutate({ item, aside: item.status !== 'set_aside' })}
                        >
                          {item.status === 'set_aside' ? 'Take it out again' : 'Set it aside'}
                        </Button>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
            <p className="muted small">
              Setting a what-if aside only moves it out of the way. Nothing in it is lost.
            </p>
          </section>
        )}
        {canWrite && (
          <form className="card stack" onSubmit={onSubmit} noValidate aria-labelledby="start-heading">
            <h2 id="start-heading">Start a what-if</h2>
            <Field label="What if…" hint="For example: What if Mara refused the guild?" error={problem}>
              {(props) => (
                <input {...props} value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
              )}
            </Field>
            <Field label="What happens in it (optional)" hint="A note to yourself; nothing is changed by it.">
              {(props) => (
                <textarea
                  {...props}
                  rows={2}
                  value={description}
                  maxLength={2000}
                  onChange={(event) => setDescription(event.target.value)}
                />
              )}
            </Field>
            <ErrorNotice error={start.error} prefix="Could not start it" />
            <div className="actions">
              <Button variant="primary" type="submit" icon="branch" disabled={start.isPending}>
                {start.isPending ? 'Starting…' : 'Start and work in it'}
              </Button>
              <span className="muted small">You can go back to your story at any time.</span>
            </div>
          </form>
        )}
      </div>
    </>
  )
}

/** One what-if beside the story: what each changed, and bringing the what-if's changes back. */
export function WhatIfPage() {
  const { branchId = '' } = useParams()
  const { scope, canWrite } = useProject()
  const entries = useEntries(scope)
  const navigate = useNavigate()
  const { whatIf: working, set: work } = useWorkingWhatIf(scope.projectId)
  const compared = useQuery({
    queryKey: scopedKey(scope, 'what-if-compare', branchId),
    queryFn: () => api.compareWhatIf(scope, branchId),
  })
  const [keep, setKeep] = useState<Record<string, 'what_if' | 'main'>>({})
  const bring = useMutation({
    mutationFn: () => {
      const choices: Record<string, 'what_if' | 'main'> = {}
      for (const item of compared.data?.conflicts ?? []) choices[item.key] = keep[item.key] ?? 'main'
      return api.bringBackWhatIf(scope, branchId, choices)
    },
    onSuccess: (result) => {
      // The proposal belongs to the story itself, so review it there.
      work(null)
      navigate(`/projects/${scope.projectId}/review/${result.mutation_id}`)
    },
  })

  const data = compared.data
  const nothing = data && data.in_what_if.length === 0
  return (
    <>
      <div className="page-head">
        <div>
          <p className="small">
            <Link to={`/projects/${scope.projectId}/what-ifs`}>All what-ifs</Link>
          </p>
          <h1>{data ? `“${data.what_if.name}”` : 'What-if'}</h1>
          <p className="lede">Compared with your story since the what-if started.</p>
        </div>
        {data && working?.id !== data.what_if.id && data.what_if.status === 'active' && (
          <div className="actions">
            <Button onClick={() => work({ id: data.what_if.id, name: data.what_if.name })}>
              Work in this what-if
            </Button>
          </div>
        )}
      </div>

      <div className="section-gap">
        {compared.isPending && <Loading>Comparing…</Loading>}
        <ErrorNotice error={compared.error} prefix="Could not compare" />
        {data && (
          <>
            <p className="muted">
              Started from version {data.what_if.started_at_version} of your story. Your story is now
              at version {data.main_version}.
            </p>
            <div className="split">
              <section className="card stack tight" aria-labelledby="mine">
                <h2 id="mine">Changed in this what-if</h2>
                <ChangeList items={data.in_what_if} empty="Nothing has been changed in it yet." />
              </section>
              <section className="card stack tight" aria-labelledby="theirs">
                <h2 id="theirs">Changed in your story since</h2>
                <ChangeList items={data.in_main} empty="Your story has not changed since." />
              </section>
            </div>

            {data.conflicts.length > 0 && (
              <section className="card stack" aria-labelledby="both">
                <h2 id="both">Changed in both</h2>
                <p className="muted small">
                  These were changed in the what-if and in your story. Choose which version to keep
                  if you bring the what-if’s changes back.
                </p>
                {data.conflicts.map((item) => (
                  <fieldset key={item.key} className="stack tight">
                    <legend>{RECORD_WORDS[item.record_type] ?? 'Record'} changed in both</legend>
                    <label className="check">
                      <input
                        type="radio"
                        name={item.key}
                        checked={(keep[item.key] ?? 'main') === 'main'}
                        onChange={() => setKeep({ ...keep, [item.key]: 'main' })}
                      />
                      <span>
                        Keep your story’s version:{' '}
                        <strong>{changeText(item.main_label ?? item.label, item.main_parts, entries.names)}</strong>
                      </span>
                    </label>
                    <label className="check">
                      <input
                        type="radio"
                        name={item.key}
                        checked={keep[item.key] === 'what_if'}
                        onChange={() => setKeep({ ...keep, [item.key]: 'what_if' })}
                      />
                      <span>
                        Use the what-if’s version:{' '}
                        <strong>{changeText(item.label, item.parts, entries.names)}</strong>
                      </span>
                    </label>
                  </fieldset>
                ))}
              </section>
            )}

            {canWrite && (
              <section className="stack tight" aria-labelledby="bring">
                <h2 id="bring">Bring it into your story</h2>
                <p>
                  The what-if’s changes become one suggested change to your story. Nothing changes
                  until you review it and approve it.
                </p>
                <ErrorNotice error={bring.error} prefix="Could not bring it back" />
                <div className="actions">
                  <Button variant="primary" disabled={bring.isPending || Boolean(nothing)} onClick={() => bring.mutate()}>
                    {bring.isPending ? 'Preparing…' : 'Suggest these changes for my story'}
                  </Button>
                  {nothing && <span className="muted small">There is nothing in it to bring back yet.</span>}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </>
  )
}
