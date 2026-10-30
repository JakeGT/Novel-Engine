import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { MAX_VOICE } from '../lib/studio'
import { scopedKey, useProject } from '../project'
import { Button, Dialog, ErrorNotice, Field, Loading } from './ui'

const EXAMPLE =
  'Past tense, close third person. Short sentences when things go wrong. No adverbs on dialogue tags. British spelling.'

/**
 * The author's notes on how they write this story. The assistant reads them first,
 * before anything else, every time it drafts a scene.
 */
export function VoiceDialog({ onClose }: { onClose: () => void }) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const key = scopedKey(scope, 'voice')
  const voice = useQuery({ queryKey: key, queryFn: () => api.voice(scope) })
  const [typed, setTyped] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (notes: string) => api.setVoice(scope, notes),
    onSuccess: (saved) => {
      client.setQueryData(key, saved)
      setTyped(null)
    },
  })
  const notes = typed ?? voice.data?.notes ?? ''
  const changed = typed !== null && typed !== (voice.data?.notes ?? '')

  return (
    <Dialog title="How you write" onClose={onClose} wide>
      <form
        className="stack"
        aria-label="How you write"
        onSubmit={(event) => {
          event.preventDefault()
          if (changed) save.mutate(notes)
        }}
      >
        <p className="muted small">
          A few notes on your style for this story: tense, whose eyes the story sees through, how
          your sentences run, words you never use. The assistant reads these first, every time it
          drafts a scene. They change nothing you have already written.
        </p>
        {voice.isPending && <Loading>Opening your notes…</Loading>}
        <ErrorNotice error={voice.error} prefix="Could not open your notes" />
        {voice.isSuccess && (
          <Field
            label="Your notes"
            hint={`${notes.length.toLocaleString()} of ${MAX_VOICE.toLocaleString()} characters. For example: ${EXAMPLE}`}
          >
            {(props) => (
              <textarea
                {...props}
                rows={8}
                value={notes}
                maxLength={MAX_VOICE}
                readOnly={!canWrite}
                placeholder={EXAMPLE}
                onChange={(event) => {
                  setTyped(event.target.value)
                  save.reset()
                }}
              />
            )}
          </Field>
        )}
        <ErrorNotice error={save.error} prefix="Not saved" />
        {save.isSuccess && !changed && (
          <p className="notice ok" role="status">
            Saved. The next draft will follow these notes.
          </p>
        )}
        <div className="actions end">
          <Button onClick={onClose}>{changed ? 'Close without saving' : 'Close'}</Button>
          {canWrite && (
            <Button type="submit" variant="primary" disabled={!changed || save.isPending}>
              {save.isPending ? 'Saving…' : 'Save how you write'}
            </Button>
          )}
        </div>
      </form>
    </Dialog>
  )
}
