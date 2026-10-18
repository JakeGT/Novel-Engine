import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type NameAnswer, type NameExample, type NameMemory } from '../api/client'
import { NameLimitField } from '../components/NameLimitField'
import { useNameChoices } from '../lib/nameChoices'
import { Badge, Button, EmptyState, ErrorNotice, Field, Loading, ReadOnlyNotice, UndoNotice } from '../components/ui'
import { defaultLimit, describeLimit, limitBody, limitIsBroken, limitProblem, type NameLimit } from '../lib/names'
import { plural, typeLabel } from '../lib/words'
import { scopedKey, useEntries, useProject } from '../project'

/**
 * Every answer the author has given about who a phrase means. An answer can hold
 * everywhere or only in part of the story; where several hold, the narrowest decides.
 * Each answer keeps the passages the author confirmed, and says when today's answers
 * would read one of them differently.
 */
export function NamesPage() {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const memory = useQuery({ queryKey: scopedKey(scope, 'names'), queryFn: () => api.nameMemory(scope) })
  const texts = useQuery({ queryKey: scopedKey(scope, 'texts'), queryFn: () => api.texts(scope) })
  const [forgotten, setForgotten] = useState<NameAnswer | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: scopedKey(scope, 'names') })

  const forget = useMutation({
    mutationFn: (answer: NameAnswer) => api.forgetName(scope, answer.id),
    onSuccess: async (_, answer) => {
      setForgotten(answer)
      await refresh()
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'text') })
    },
  })
  const undo = useMutation({
    mutationFn: async (answer: NameAnswer) => {
      const back = await api.decideName(scope, {
        phrase: answer.phrase,
        verdict: answer.verdict as 'is' | 'is_not' | 'ignore',
        entity_id: answer.entity_id,
        source_id: answer.source_id,
        what_if_only: Boolean(answer.branch_id),
        from_scene_id: answer.from_scene_id ?? null,
        until_scene_id: answer.until_scene_id ?? null,
        viewpoint_id: answer.viewpoint_id ?? null,
      })
      // Examples come back as the words they kept.
      for (const example of answer.examples ?? []) {
        await api.addNameExample(scope, back.id, {
          quote: example.quote,
          means: example.means,
          scene_id: example.scene_id ?? null,
        })
      }
    },
    onSuccess: async () => {
      setForgotten(null)
      await refresh()
    },
  })

  const data = memory.data
  // Answers kept to another what-if never apply here, so they are only counted.
  const here = (data?.items ?? []).filter((item) => !item.branch_id || item.branch_id === scope.branchId)
  const elsewhere = (data?.items.length ?? 0) - here.length
  const phrases = groupByPhrase(here)
  const textTitles = Object.fromEntries((texts.data?.items ?? []).map((text) => [text.id, text.title]))

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Names</h1>
          <p className="lede">
            What you have said the names and nicknames in your writing mean. An answer can hold everywhere,
            or only in part of the story: one text, a stretch of scenes, or scenes seen by one character.
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to={`/projects/${scope.projectId}/texts`}>
            Your writing
          </Link>
        </div>
      </div>

      <div className="section-gap stack">
        <ReadOnlyNotice canWrite={canWrite} />
        {canWrite && <AnswerForm onDone={refresh} />}
        {forgotten && (
          <UndoNotice onUndo={() => undo.mutate(forgotten)} busy={undo.isPending}>
            Forgot what “{forgotten.phrase}” means
            {forgotten.examples?.length ? ` and its ${plural(forgotten.examples.length, 'example')}` : ''}.
          </UndoNotice>
        )}
        <ErrorNotice error={forget.error ?? undo.error} prefix="Could not change that" />
        {memory.isPending && <Loading>Opening your answers…</Loading>}
        <ErrorNotice error={memory.error} prefix="Could not open your answers" />

        {data && here.length === 0 && (
          <EmptyState icon="feather" title="No names answered yet">
            When your writing uses a name the story bible does not know, you are asked who it is. Your answers
            are kept here, and you can give one yourself above.
          </EmptyState>
        )}

        {data &&
          phrases.map(([phrase, answers]) => (
            <section key={phrase} className="card stack tight" aria-labelledby={`phrase-${answers[0].id}`}>
              <h2 id={`phrase-${answers[0].id}`}>“{phrase}”</h2>
              <ul className="rows flush name-answers">
                {answers.map((answer) => (
                  <AnswerRow
                    key={answer.id}
                    answer={answer}
                    memory={data}
                    textTitles={textTitles}
                    canWrite={canWrite}
                    busy={forget.isPending}
                    onForget={() => forget.mutate(answer)}
                    onChanged={refresh}
                  />
                ))}
              </ul>
            </section>
          ))}
        {elsewhere > 0 && (
          <p className="muted small">
            {elsewhere === 1 ? 'One more answer is' : `${elsewhere} more answers are`} kept to another what-if and
            does not apply here.
          </p>
        )}
      </div>
    </>
  )
}

function groupByPhrase(items: NameAnswer[]): [string, NameAnswer[]][] {
  const groups = new Map<string, NameAnswer[]>()
  for (const item of items) {
    const key = item.phrase.toLowerCase()
    groups.set(key, [...(groups.get(key) ?? []), item])
  }
  return [...groups.values()]
    .map((answers): [string, NameAnswer[]] => [answers[0].phrase, answers])
    .sort((a, b) => a[0].localeCompare(b[0]))
}

function meaning(answer: NameAnswer, names: Record<string, string>): string {
  const who = answer.entity_id ? (names[answer.entity_id] ?? 'an entry no longer in the story') : ''
  if (answer.verdict === 'ignore') return 'Not a name'
  if (answer.verdict === 'is_not') return `Not ${who}`
  return `Means ${who}`
}

function AnswerRow({
  answer,
  memory,
  textTitles,
  canWrite,
  busy,
  onForget,
  onChanged,
}: {
  answer: NameAnswer
  memory: NameMemory
  textTitles: Record<string, string>
  canWrite: boolean
  busy: boolean
  onForget: () => void
  onChanged: () => Promise<void>
}) {
  const [adding, setAdding] = useState(false)
  const words = { scenes: memory.scenes ?? {}, names: memory.names, texts: textTitles }
  const broken = limitIsBroken(answer, words.scenes)
  const examples = answer.examples ?? []
  return (
    <li className="row name-answer">
      <div className="grow stack tight">
        <p>
          <strong>{meaning(answer, memory.names)}</strong>
          <span className="muted"> · {describeLimit(answer, words)}</span>
        </p>
        {broken && (
          <p className="small warn-text">
            One of its scenes is no longer in the plan, so this answer does not apply anywhere. Give it again with
            another scene, or forget it.
          </p>
        )}
        {examples.length > 0 && (
          <ul className="name-examples" aria-label={`Examples for ${meaning(answer, memory.names)}`}>
            {examples.map((example) => (
              <ExampleRow
                key={example.id}
                answer={answer}
                example={example}
                memory={memory}
                canWrite={canWrite}
                onChanged={onChanged}
              />
            ))}
          </ul>
        )}
        {canWrite &&
          (adding ? (
            <ExampleForm answer={answer} memory={memory} onClose={() => setAdding(false)} onChanged={onChanged} />
          ) : (
            <div className="actions">
              <Button size="sm" variant="ghost" icon="plus" onClick={() => setAdding(true)}>
                Add an example
              </Button>
            </div>
          ))}
      </div>
      {canWrite && (
        <div className="row-actions">
          <Button size="sm" variant="danger-quiet" disabled={busy} onClick={onForget}>
            Forget this answer
          </Button>
        </div>
      )}
    </li>
  )
}

function Quote({ example }: { example: NameExample }) {
  const at = example.at
  if (!at || at.length !== 2) return <>“{example.quote}”</>
  return (
    <>
      “{example.quote.slice(0, at[0])}
      <mark>{example.quote.slice(at[0], at[1])}</mark>
      {example.quote.slice(at[1])}”
    </>
  )
}

function ExampleRow({
  answer,
  example,
  memory,
  canWrite,
  onChanged,
}: {
  answer: NameAnswer
  example: NameExample
  memory: NameMemory
  canWrite: boolean
  onChanged: () => Promise<void>
}) {
  const { scope } = useProject()
  const remove = useMutation({
    mutationFn: () => api.removeNameExample(scope, answer.id, example.id),
    onSuccess: onChanged,
  })
  const who = answer.entity_id ? memory.names[answer.entity_id] : null
  const now = (example.now ?? []).map((id) => memory.names[id] ?? 'an entry').join(' or ')
  const scene = example.scene_id ? memory.scenes?.[example.scene_id] : null
  return (
    <li className="name-example">
      <blockquote className="small">
        <Quote example={example} />
      </blockquote>
      <div className="actions">
        <span className="muted small">
          {answer.verdict === 'ignore'
            ? 'Not a name here'
            : example.means
              ? `Here it means ${who ?? 'the entry'}`
              : `Here it does not mean ${who ?? 'the entry'}`}
          {scene ? ` · in “${scene}”` : ''}
        </span>
        {example.holds === true && <Badge tone="ok">Still reads this way</Badge>}
        {example.holds === false && (
          <Badge tone="warn">{now ? `Now reads as ${now}` : 'Now reads as nobody'}</Badge>
        )}
        {canWrite && (
          <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>
            Remove example
          </Button>
        )}
      </div>
      <ErrorNotice error={remove.error} prefix="Could not remove that example" />
    </li>
  )
}

function ExampleForm({
  answer,
  memory,
  onClose,
  onChanged,
}: {
  answer: NameAnswer
  memory: NameMemory
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const { scope } = useProject()
  const choices = useNameChoices()
  const [quote, setQuote] = useState('')
  const [means, setMeans] = useState(answer.verdict === 'is')
  const [sceneId, setSceneId] = useState('')
  const who = answer.entity_id ? (memory.names[answer.entity_id] ?? 'the entry') : null
  const uses = quote.toLowerCase().includes(answer.phrase.toLowerCase())
  const add = useMutation({
    mutationFn: () =>
      api.addNameExample(scope, answer.id, {
        quote: quote.trim(),
        means: answer.verdict === 'ignore' ? false : means,
        scene_id: sceneId || null,
      }),
    onSuccess: async () => {
      await onChanged()
      onClose()
    },
  })
  return (
    <form
      className="stack tight name-example-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (uses) add.mutate()
      }}
    >
      <Field
        label="The passage"
        hint={`A sentence or two that uses “${answer.phrase}”.`}
        error={quote.trim() && !uses ? `The passage has to use “${answer.phrase}”.` : undefined}
      >
        {(props) => (
          <textarea {...props} rows={2} maxLength={400} value={quote} onChange={(event) => setQuote(event.target.value)} />
        )}
      </Field>
      {who && answer.verdict !== 'ignore' && (
        <fieldset className="choice-row">
          <legend className="sr-only">What it means here</legend>
          <label className="check small">
            <input type="radio" name={`means-${answer.id}`} checked={means} onChange={() => setMeans(true)} />
            Here it means {who}
          </label>
          <label className="check small">
            <input type="radio" name={`means-${answer.id}`} checked={!means} onChange={() => setMeans(false)} />
            Here it does not mean {who}
          </label>
        </fieldset>
      )}
      {choices.scenes.length > 0 && (
        <Field label="From the scene" hint="Optional. Lets the example be checked where it was written.">
          {(props) => (
            <select {...props} value={sceneId} onChange={(event) => setSceneId(event.target.value)}>
              <option value="">Not from a scene</option>
              {choices.scenes.map((scene, index) => (
                <option key={scene.id} value={scene.id}>
                  {index + 1}. {scene.label}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      <ErrorNotice error={add.error} prefix="Could not keep that example" />
      <div className="actions">
        <Button type="submit" size="sm" variant="primary" disabled={!uses || add.isPending}>
          Keep this example
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function AnswerForm({ onDone }: { onDone: () => Promise<void> }) {
  const { scope } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const choices = useNameChoices()
  const [phrase, setPhrase] = useState('')
  const [who, setWho] = useState('')
  const [limit, setLimit] = useState<NameLimit>(() => defaultLimit(choices.inWhatIf))
  const [tried, setTried] = useState(false)
  const problem = limitProblem(limit, choices.order)
  const active = entries.items.filter((entry) => entry.lifecycle === 'active')
  const decide = useMutation({
    mutationFn: () =>
      api.decideName(scope, {
        phrase: phrase.trim(),
        verdict: who === 'ignore' ? 'ignore' : 'is',
        entity_id: who === 'ignore' ? null : who,
        ...limitBody(limit, { inWhatIf: choices.inWhatIf }),
      }),
    onSuccess: async () => {
      setPhrase('')
      setWho('')
      setTried(false)
      await onDone()
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'text') })
    },
  })
  const ready = phrase.trim().length > 0 && who !== ''
  return (
    <section className="card stack tight" aria-labelledby="answer-heading">
      <h2 id="answer-heading">Answer a name</h2>
      <form
        className="stack tight"
        onSubmit={(event) => {
          event.preventDefault()
          setTried(true)
          if (ready && !problem) decide.mutate()
        }}
      >
        <div className="name-answer-fields">
          <Field label="The words" hint="As your writing uses them, such as “the captain”.">
            {(props) => (
              <input {...props} value={phrase} maxLength={120} onChange={(event) => setPhrase(event.target.value)} />
            )}
          </Field>
          <Field label="Means">
            {(props) => (
              <select {...props} value={who} onChange={(event) => setWho(event.target.value)}>
                <option value="">Choose an entry…</option>
                {active.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} ({typeLabel(entry.type)})
                  </option>
                ))}
                <option value="ignore">Not a name</option>
              </select>
            )}
          </Field>
        </div>
        <NameLimitField
          value={limit}
          onChange={setLimit}
          scenes={choices.scenes}
          characters={choices.characters}
          whatIfName={choices.whatIfName}
          problem={tried ? problem : null}
          disabled={decide.isPending}
        />
        <ErrorNotice error={decide.error} prefix="Could not remember that" />
        <div className="actions">
          <Button type="submit" variant="primary" disabled={!ready || decide.isPending}>
            Remember this answer
          </Button>
        </div>
      </form>
    </section>
  )
}
