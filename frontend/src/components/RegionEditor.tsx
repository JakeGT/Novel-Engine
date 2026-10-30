import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Link } from 'react-router-dom'
import { pictureApi, type PartChange, type Picture, type PicturePart } from '../api/client'
import { entryPath, scopedKey, useEntries, useProject } from '../project'
import { Button, ErrorNotice, UndoNotice } from './ui'
import { boxBetween, boxProblem, middleBox, type PixelBox } from '../lib/regions'

const SIDES: { key: keyof PixelBox; label: string }[] = [
  { key: 'x', label: 'From the left' },
  { key: 'y', label: 'From the top' },
  { key: 'width', label: 'Width' },
  { key: 'height', label: 'Height' },
]

/**
 * Drawing on the picture with a pointer (press, drag, let go). The same box can always be
 * typed instead (WCAG 2.2, 2.5.7 Dragging Movements): this layer is a shortcut, hidden
 * from assistive technology, and the numbers beside the form are the way that always works.
 */
export function DrawLayer({
  size,
  onDraw,
}: {
  size: { width: number; height: number }
  onDraw: (box: PixelBox) => void
}) {
  const start = useRef<{ x: number; y: number } | null>(null)
  const at = (event: ReactPointerEvent<HTMLDivElement>) => {
    const frame = event.currentTarget.getBoundingClientRect()
    const fx = Math.min(1, Math.max(0, (event.clientX - frame.left) / Math.max(1, frame.width)))
    const fy = Math.min(1, Math.max(0, (event.clientY - frame.top) / Math.max(1, frame.height)))
    return { x: fx * size.width, y: fy * size.height }
  }
  const boxFrom = (a: { x: number; y: number }, b: { x: number; y: number }) => boxBetween(a, b, size)
  return (
    <div
      className="picture-draw"
      aria-hidden="true"
      data-testid="picture-draw"
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture?.(event.pointerId)
        start.current = at(event)
      }}
      onPointerMove={(event) => {
        if (start.current) onDraw(boxFrom(start.current, at(event)))
      }}
      onPointerUp={(event) => {
        if (start.current) onDraw(boxFrom(start.current, at(event)))
        start.current = null
      }}
      onPointerCancel={() => {
        start.current = null
      }}
    />
  )
}

function partName(part: PicturePart): string {
  return part.label || part.entity_name || 'A part with no name'
}

function PartForm({
  picture,
  part,
  draft,
  onDraft,
  onDone,
}: {
  picture: Picture
  part: PicturePart | null
  draft: PixelBox
  onDraft: (box: PixelBox) => void
  onDone: (message: string) => void
}) {
  const { scope } = useProject()
  const client = useQueryClient()
  const entries = useEntries(scope)
  const id = useId()
  const size = { width: picture.width ?? 1, height: picture.height ?? 1 }
  const [label, setLabel] = useState(part?.label ?? '')
  const [entityId, setEntityId] = useState(part?.entity_id ?? '')
  // What was typed, kept as typed (an empty field is not a zero).
  const [typed, setTyped] = useState<Record<keyof PixelBox, string> | null>(null)
  const shown = typed ?? {
    x: String(draft.x),
    y: String(draft.y),
    width: String(draft.width),
    height: String(draft.height),
  }
  const problem = boxProblem(
    { x: Number(shown.x), y: Number(shown.y), width: Number(shown.width), height: Number(shown.height) },
    size,
  )
  const [tried, setTried] = useState(false)
  const choices = entries.items.filter((entry) => entry.lifecycle !== 'retired')
  const save = useMutation({
    mutationFn: () => {
      const change: PartChange = { ...draft, label: label.trim() || null, entity_id: entityId || null }
      return part
        ? pictureApi.changePart(scope, picture.asset_id, picture.version.id, part.id, change)
        : pictureApi.markPart(scope, picture.asset_id, picture.version.id, change)
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture-page', picture.asset_id) })
      const name = saved.label || entries.names.get(saved.entity_id ?? '') || 'the part'
      onDone(part ? `Saved the changes to “${name}”.` : `Marked “${name}”.`)
    },
  })
  return (
    <form
      className="region-form stack tight"
      aria-labelledby={`${id}-title`}
      onSubmit={(event) => {
        event.preventDefault()
        setTried(true)
        if (!problem) save.mutate()
      }}
    >
      <h3 id={`${id}-title`} className="region-form-title">
        {part ? `Change “${partName(part)}”` : 'Mark a part'}
      </h3>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name (optional)</label>
        <input
          id={`${id}-name`}
          value={label}
          maxLength={120}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Mara’s scar"
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-entry`}>Who or what it shows (optional)</label>
        <select id={`${id}-entry`} value={entityId} onChange={(event) => setEntityId(event.target.value)}>
          <option value="">No entry</option>
          {choices.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </div>
      <fieldset className="region-sides" aria-describedby={`${id}-where`}>
        <legend>Where it is, in pixels</legend>
        <p id={`${id}-where`} className="hint">
          Drag across the picture to draw it, or type where it is. The picture is {size.width} by {size.height} pixels.
        </p>
        <div className="region-side-grid">
          {SIDES.map((side) => (
            <div className="field" key={side.key}>
              <label htmlFor={`${id}-${side.key}`}>{side.label}</label>
              <input
                id={`${id}-${side.key}`}
                type="number"
                inputMode="numeric"
                min={side.key === 'width' || side.key === 'height' ? 1 : 0}
                max={side.key === 'x' || side.key === 'width' ? size.width : size.height}
                step={1}
                value={shown[side.key]}
                aria-invalid={tried && problem ? true : undefined}
                aria-describedby={tried && problem ? `${id}-problem` : undefined}
                onChange={(event) => {
                  const next = { ...shown, [side.key]: event.target.value }
                  setTyped(next)
                  const number = Number(event.target.value)
                  if (event.target.value !== '' && Number.isFinite(number)) {
                    onDraft({ ...draft, [side.key]: Math.round(number) })
                  }
                }}
                onBlur={() => setTyped(null)}
              />
            </div>
          ))}
        </div>
        {tried && problem && (
          <p id={`${id}-problem`} className="field-error" role="alert">
            {problem}
          </p>
        )}
      </fieldset>
      <ErrorNotice error={save.error} prefix={part ? 'Could not save the changes' : 'Could not mark the part'} />
      <div className="picture-actions">
        <Button onClick={() => onDone('')}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : part ? 'Save changes' : 'Mark this part'}
        </Button>
      </div>
    </form>
  )
}

/**
 * The parts of a picture the author marked (AST-007): a face in a crowd, a sign on a
 * wall, each with a name and the entry it shows. Drawn on the picture or typed as
 * numbers. The assistant describes each part, the search index finds pictures by them,
 * and a note of the pictures check can point at one. Nothing in the story changes.
 */
export function RegionEditor({
  picture,
  editing,
  draft,
  onEdit,
  onDraft,
  shown,
  onShow,
}: {
  picture: Picture
  /** The part being changed ('new' for a new one), or null when none is. */
  editing: string | null
  draft: PixelBox | null
  onEdit: (partId: string | null, start: PixelBox | null) => void
  onDraft: (box: PixelBox) => void
  shown: string | null
  onShow: (partId: string | null) => void
}) {
  const { scope, canWrite } = useProject()
  const client = useQueryClient()
  const [said, setSaid] = useState('')
  const [removed, setRemoved] = useState<PicturePart | null>(null)
  const parts = picture.parts ?? []
  const ready = Boolean(picture.width && picture.height)
  const size = { width: picture.width ?? 1, height: picture.height ?? 1 }
  const refresh = () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'picture-page', picture.asset_id) })
  const remove = useMutation({
    mutationFn: (part: PicturePart) => pictureApi.removePart(scope, picture.asset_id, picture.version.id, part.id),
    onSuccess: (_gone, part) => {
      setRemoved(part)
      setSaid('')
      if (shown === part.id) onShow(null)
      refresh()
    },
  })
  const undo = useMutation({
    mutationFn: (part: PicturePart) =>
      pictureApi.markPart(scope, picture.asset_id, picture.version.id, {
        x: part.x,
        y: part.y,
        width: part.width,
        height: part.height,
        label: part.label ?? null,
        entity_id: part.entity_id ?? null,
      }),
    onSuccess: (_made, part) => {
      setRemoved(null)
      setSaid(`“${partName(part)}” is marked again.`)
      refresh()
    },
  })
  const changing = editing && editing !== 'new' ? parts.find((part) => part.id === editing) ?? null : null
  return (
    <section className="picture-section" aria-labelledby="picture-parts">
      <h2 id="picture-parts">Marked parts</h2>
      <p className="hint">
        Mark a part of the picture, such as a face or a sign, to name it and say who or what it shows. The
        assistant describes each marked part, search finds the picture by it, and a note from the pictures
        check can point at it. Nothing in the story changes.
      </p>
      {!ready && <p className="muted">Parts can be marked once the picture is ready.</p>}
      {ready && parts.length === 0 && editing === null && <p className="muted">No parts are marked yet.</p>}
      {parts.length > 0 && (
        <ol className="region-list">
          {parts.map((part, index) => (
            <li key={part.id} className={`region-item ${shown === part.id ? 'shown' : ''}`}>
              <span className="region-number" aria-hidden="true">
                {index + 1}
              </span>
              <div className="region-words">
                <strong>{partName(part)}</strong>
                <span className="small muted">
                  {part.entity_id && part.entity_name ? (
                    <>
                      Shows <Link to={entryPath(scope, part.entity_id)}>{part.entity_name}</Link> ·{' '}
                    </>
                  ) : null}
                  {part.width} by {part.height} pixels, {part.x} from the left and {part.y} from the top
                </span>
              </div>
              <div className="picture-actions region-actions">
                <Button size="sm" variant="ghost" aria-pressed={shown === part.id} onClick={() => onShow(shown === part.id ? null : part.id)}>
                  {shown === part.id ? 'Hide where' : 'Show where'}
                </Button>
                {canWrite && editing === null && (
                  <Button
                    size="sm"
                    onClick={() => {
                      setSaid('')
                      setRemoved(null)
                      onEdit(part.id, { x: part.x, y: part.y, width: part.width, height: part.height })
                    }}
                  >
                    Change
                  </Button>
                )}
                {canWrite && editing === null && (
                  <Button
                    size="sm"
                    variant="danger-quiet"
                    className="region-remove"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(part)}
                  >
                    Remove
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {removed && (
        <UndoNotice busy={undo.isPending} onUndo={() => undo.mutate(removed)}>
          Removed “{partName(removed)}”. Notes that pointed at it keep where it was.
        </UndoNotice>
      )}
      {said && (
        <p className="small" role="status">
          {said}
        </p>
      )}
      <ErrorNotice error={remove.error ?? undo.error} prefix="Could not change the marked parts" />
      {canWrite && ready && editing === null && (
        <div className="picture-actions">
          <Button
            icon="plus"
            onClick={() => {
              setSaid('')
              setRemoved(null)
              onEdit('new', middleBox(size))
            }}
          >
            Mark a part
          </Button>
        </div>
      )}
      {canWrite && ready && editing !== null && draft && (
        <PartForm
          key={editing}
          picture={picture}
          part={changing}
          draft={draft}
          onDraft={onDraft}
          onDone={(message) => {
            setSaid(message)
            onEdit(null, null)
          }}
        />
      )}
    </section>
  )
}
