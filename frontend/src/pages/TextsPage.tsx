import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type SourceText } from '../api/client'
import { Button, Dialog, EmptyState, ErrorNotice, Field, Loading, ReadOnlyNotice } from '../components/ui'
import { wordCount, wordsFromLength } from '../lib/texts'
import { timeAgo } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'

/** The longest text kept in one piece; the server holds the same line. */
const MAX_LENGTH = 400_000

/**
 * Prose the author has already written. Bringing it in is another way to fill in
 * the story bible: the names in it are found straight away, and anything taken
 * from it points back at the words.
 */
export function TextsPage() {
  const { scope, canWrite } = useProject()
  const [adding, setAdding] = useState(false)
  const texts = useQuery({ queryKey: scopedKey(scope, 'texts'), queryFn: () => api.texts(scope) })
  const items = texts.data?.items ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Your writing</h1>
          <p className="lede">
            Bring in a chapter or a scene you have already written. The names in it are found
            straight away, and anything added to your story bible from it points back at your words.
          </p>
        </div>
        {canWrite && (
          <div className="actions">
            <Button variant="primary" icon="plus" onClick={() => setAdding(true)}>
              Bring in a text
            </Button>
          </div>
        )}
      </div>

      <div className="section-gap">
        <ReadOnlyNotice canWrite={canWrite} />
        {texts.isPending && <Loading>Opening your writing…</Loading>}
        <ErrorNotice error={texts.error} prefix="Could not open your writing" />

        {texts.isSuccess && items.length === 0 && (
          <EmptyState
            icon="feather"
            title="Nothing brought in yet"
            action={canWrite ? <Button onClick={() => setAdding(true)}>Bring in a text</Button> : undefined}
          >
            Paste a scene, or choose a plain text or Markdown file. Your words are kept exactly as
            you give them, and nothing is added to your story until you say so.
          </EmptyState>
        )}

        {items.length > 0 && (
          <ul className="rows card flush">
            {items.map((item) => (
              <TextRow key={item.id} item={item} projectId={scope.projectId} />
            ))}
          </ul>
        )}
      </div>

      {adding && <AddText onClose={() => setAdding(false)} />}
    </>
  )
}

function TextRow({ item, projectId }: { item: SourceText; projectId: string }) {
  return (
    <li className="row">
      <Link className="grow" to={`/projects/${projectId}/texts/${item.id}`}>
        <strong>{item.title}</strong>
      </Link>
      <span className="muted small">
        About {plural(wordsFromLength(item.length), 'word')}
        {item.versions > 1 ? ` · ${plural(item.versions, 'version')}` : ''} · brought in {timeAgo(item.created_at)}
      </span>
    </li>
  )
}

function AddText({ onClose }: { onClose: () => void }) {
  const { scope } = useProject()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [kind, setKind] = useState<'paste' | 'markdown'>('paste')
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)

  const add = useMutation({
    mutationFn: () => api.addText(scope, { title: title.trim(), content, kind }),
    onSuccess: async (made) => {
      await client.invalidateQueries({ queryKey: scopedKey(scope, 'texts') })
      navigate(`/projects/${scope.projectId}/texts/${made.id}`)
    },
  })

  const choose = async (file: File | undefined) => {
    if (!file) return
    setFileProblem(null)
    if (file.size > MAX_LENGTH * 4) return setFileProblem('That file is too long to bring in as one text.')
    const text = await file.text()
    if (text.includes('\u0000')) return setFileProblem('That does not look like a plain text file.')
    setContent(text)
    setKind(/\.(md|markdown)$/i.test(file.name) ? 'markdown' : 'paste')
    if (!title.trim()) setTitle(file.name.replace(/\.[^.]+$/, ''))
  }

  const tooLong = content.length > MAX_LENGTH
  const ready = title.trim().length > 0 && content.trim().length > 0 && !tooLong

  return (
    <Dialog title="Bring in a text" onClose={onClose} wide>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (ready) add.mutate()
        }}
      >
        <Field label="What to call it" hint="A chapter or scene title is enough.">
          {(props) => <input {...props} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} autoFocus />}
        </Field>
        <Field
          label="The text"
          hint={
            content.trim()
              ? `${plural(wordCount(content), 'word')}. Kept exactly as it is here.`
              : 'Paste it here, or choose a file below.'
          }
          error={tooLong ? 'This is too long for one text. Bring it in a chapter at a time.' : undefined}
        >
          {(props) => (
            <textarea
              {...props}
              className="text-paste"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={12}
            />
          )}
        </Field>
        <div className="actions">
          <input
            ref={picker}
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            className="sr-only"
            aria-label="Choose a text file"
            onChange={(event) => void choose(event.target.files?.[0])}
          />
          <Button size="sm" onClick={() => picker.current?.click()}>
            Choose a file
          </Button>
          <span className="muted small">Plain text or Markdown. Headings split it into parts.</span>
        </div>
        {fileProblem && (
          <p className="notice danger" role="alert">
            {fileProblem}
          </p>
        )}
        <ErrorNotice error={add.error} prefix="Could not bring it in" />
        <div className="actions end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready || add.isPending}>
            {add.isPending ? 'Bringing it in…' : 'Bring it in'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
