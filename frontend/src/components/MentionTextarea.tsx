import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
  type TextareaHTMLAttributes,
} from 'react'
import { insertMention, mentionAt, suggest, type MentionOption, type MentionQuery } from '../lib/mentions'
import { typeLabel } from '../lib/words'
import { useEntries, useProject } from '../project'

type TextareaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'defaultValue'>

// What a hidden copy of the box needs to lay its words out the same way.
const COPIED = [
  'boxSizing',
  'width',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'fontFamily',
  'fontSize',
  'fontStyle',
  'fontWeight',
  'fontVariant',
  'fontStretch',
  'lineHeight',
  'letterSpacing',
  'wordSpacing',
  'textIndent',
  'textTransform',
  'tabSize',
] as const

/** Where a position in a text box sits on screen, relative to the box: a hidden copy measures it. */
function caretPoint(box: HTMLTextAreaElement, position: number): { top: number; left: number; height: number } {
  const style = window.getComputedStyle(box)
  const copy = document.createElement('div')
  for (const name of COPIED) copy.style[name] = style[name]
  copy.style.position = 'absolute'
  copy.style.visibility = 'hidden'
  copy.style.whiteSpace = 'pre-wrap'
  copy.style.overflowWrap = 'break-word'
  copy.style.top = '0'
  copy.style.left = '-9999px'
  copy.textContent = box.value.slice(0, position)
  const marker = document.createElement('span')
  marker.textContent = box.value.slice(position) || '.'
  copy.appendChild(marker)
  document.body.appendChild(copy)
  const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4 || 20
  const point = {
    top: marker.offsetTop - box.scrollTop,
    left: marker.offsetLeft - box.scrollLeft,
    height: lineHeight,
  }
  copy.remove()
  return point
}

/**
 * A text box where typing @ offers entries from the story bible by name and alias
 * (CTX-003). Choosing one writes @Name, or @"Two Words", the marking the name finder
 * reads. Arrows move through the names, Enter or Tab puts one in, Escape closes the list.
 *
 * Accessibility (WAI-ARIA APG combobox, "list autocomplete" example): focus stays in the
 * box; the names are a listbox it controls, and aria-activedescendant points at the one
 * picked out. The box keeps its own text-box role so it stays a multi-line field; a polite
 * status says how many names match, as the Orange accessibility guidelines suggest for
 * autocompletion. The words go in through the browser's own editing command where it
 * exists, so Ctrl Z takes the name back out.
 */
export function MentionTextarea({
  value,
  onValueChange,
  ref,
  className,
  onKeyDown,
  onBlur,
  readOnly,
  ...rest
}: TextareaProps & {
  value: string
  onValueChange: (value: string) => void
  ref?: Ref<HTMLTextAreaElement>
}) {
  const { scope } = useProject()
  const entries = useEntries(scope)
  const box = useRef<HTMLTextAreaElement | null>(null)
  const listId = useId()
  const [query, setQuery] = useState<MentionQuery | null>(null)
  const [active, setActive] = useState(0)
  // Closed with Escape, or just chosen: stays closed until what follows the @ changes. A ref,
  // not state: putting a name in reports the change at once, before React renders again,
  // and that report must already see the list as closed or it opens again.
  const closed = useRef<{ start: number; text: string } | null>(null)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const caretAfter = useRef<number | null>(null)

  const options: MentionOption[] = query ? suggest(entries.items, query.text) : []
  const open = query !== null && !readOnly
  const showing = open && options.length > 0
  const current = Math.min(active, Math.max(options.length - 1, 0))
  const optionId = (index: number) => `${listId}-option-${index}`

  const setRefs = (node: HTMLTextAreaElement | null) => {
    box.current = node
    if (typeof ref === 'function') ref(node)
    else if (ref) (ref as { current: HTMLTextAreaElement | null }).current = node
  }

  /** Looks again at the words before the caret. */
  const look = (text: string, caret: number | null) => {
    const node = box.current
    if (readOnly || caret === null || (node && node.selectionStart !== node.selectionEnd)) {
      setQuery(null)
      return
    }
    const found = mentionAt(text, caret)
    const shut = closed.current
    if (!found || (shut && shut.start === found.start && shut.text === found.text)) {
      if (!found) closed.current = null
      setQuery(null)
      return
    }
    if (shut && shut.start !== found.start) closed.current = null
    if (!query || query.start !== found.start || query.text !== found.text) setActive(0)
    setQuery(found)
    if (node) {
      const point = caretPoint(node, found.start)
      const width = Math.min(18 * 16, node.offsetWidth)
      const left = Math.max(0, Math.min(node.offsetLeft + point.left, node.offsetLeft + node.offsetWidth - width))
      const top = node.offsetTop + Math.min(Math.max(point.top, 0), node.clientHeight - point.height) + point.height + 4
      setPlace({ top, left })
    }
  }

  // After a fallback insertion, the caret goes after the name once the new words are in.
  useLayoutEffect(() => {
    const node = box.current
    if (caretAfter.current === null || !node || node.value !== value) return
    node.setSelectionRange(caretAfter.current, caretAfter.current)
    caretAfter.current = null
  }, [value])

  const choose = (option: MentionOption) => {
    const node = box.current
    if (!query || !node) return
    const next = insertMention(value, query, option.label)
    closed.current = { start: query.start, text: option.label }
    setQuery(null)
    node.focus()
    node.setSelectionRange(query.start, query.end)
    // The browser's own command keeps Ctrl Z working and reports the change as typing.
    const typed = typeof document.execCommand === 'function' && document.execCommand('insertText', false, next.inserted)
    if (!typed || node.value !== next.value) {
      caretAfter.current = next.caret
      onValueChange(next.value)
    }
  }

  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Keys pressed while an input method is putting words together are its own; Safari
    // reports them with key code 229 and without isComposing.
    const composing = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
    if (open && !composing) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closed.current = { start: query.start, text: query.text }
        setQuery(null)
        return
      }
      if (showing) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          const step = event.key === 'ArrowDown' ? 1 : -1
          setActive((current + step + options.length) % options.length)
          return
        }
        if ((event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault()
          choose(options[current])
          return
        }
      }
    }
    onKeyDown?.(event)
  }

  const count = options.length
  const none = !open
    ? ''
    : query.text
      ? `No entry is called “${query.text}”. Keep typing to write it as it is.`
      : 'Your story bible has no entries to name yet.'
  const status = !open
    ? ''
    : count === 0
      ? none
      : `${count === 1 ? '1 entry matches' : `${count} entries match`}. Up and down arrows to choose, Enter to put one in, Escape to close.`

  return (
    <div className="mention-box">
      <textarea
        {...rest}
        ref={setRefs}
        className={className}
        value={value}
        readOnly={readOnly}
        aria-autocomplete={readOnly ? undefined : 'list'}
        aria-controls={showing ? listId : undefined}
        aria-activedescendant={showing ? optionId(current) : undefined}
        data-suggestions-open={open ? 'true' : undefined}
        data-suggestions-showing={showing ? 'true' : undefined}
        onChange={(event) => {
          onValueChange(event.target.value)
          look(event.target.value, event.target.selectionStart)
        }}
        onSelect={(event) => look(event.currentTarget.value, event.currentTarget.selectionStart)}
        onKeyDown={keyDown}
        onBlur={(event) => {
          setQuery(null)
          onBlur?.(event)
        }}
      />
      {open && (
        <div className="mention-pop" style={place ? { top: place.top, left: place.left } : undefined}>
          {showing ? (
            <ul id={listId} role="listbox" aria-label="Entries in your story bible" className="mention-list">
              {options.map((option, index) => (
                <li
                  key={option.id}
                  id={optionId(index)}
                  role="option"
                  aria-selected={index === current}
                  className="mention-option"
                  // Keeps the caret in the text while the name is chosen with the mouse.
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(option)}
                >
                  <span className="mention-name">{option.name}</span>
                  <span className="mention-meta">
                    {typeLabel(option.type)}
                    {option.via ? ` · as “${option.via}”` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mention-none">{none}</p>
          )}
          {showing && <p className="mention-keys" aria-hidden="true">Enter or Tab to put it in · Esc to close</p>}
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">
        {status}
      </span>
    </div>
  )
}
