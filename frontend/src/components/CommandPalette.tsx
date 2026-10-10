import { useQuery } from '@tanstack/react-query'
import { useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Scope } from '../api/client'
import { typeLabel } from '../lib/words'
import { entryPath, scopedKey, useEntries } from '../project'
import { Icon, type IconName } from './icons'
import { Dialog, TypeAvatar } from './ui'

interface Command {
  key: string
  label: string
  meta?: string
  icon?: IconName
  type?: string
  run: () => void
}

interface Props {
  scope: Scope
  canWrite: boolean
  onClose: () => void
  onNewEntry: (type?: string) => void
}

/** Jump to any entry or page, or start a new entry, from the keyboard. */
export function CommandPalette({ scope, canWrite, onClose, onNewEntry }: Props) {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [index, setIndex] = useState(0)
  const term = text.trim()
  const entries = useEntries(scope)
  // The server also matches aliases and short names, and is not capped by what is loaded.
  const search = useQuery({
    queryKey: scopedKey(scope, 'entity-search', term),
    queryFn: () => api.searchEntities(scope, { q: term, limit: 12 }),
    enabled: term.length >= 2,
  })

  const base = `/projects/${scope.projectId}`
  const go = (path: string) => () => {
    onClose()
    navigate(path)
  }
  const pages: Command[] = [
    { key: 'p-home', label: 'Home', icon: 'home', run: go(base) },
    { key: 'p-bible', label: 'Story bible', icon: 'book', run: go(`${base}/bible`) },
    { key: 'p-review', label: 'Review suggested changes', icon: 'inbox', run: go(`${base}/review`) },
    { key: 'p-history', label: 'History', icon: 'clock', run: go(`${base}/history`) },
    { key: 'p-customize', label: 'Types and fields', icon: 'sliders', run: go(`${base}/customize`) },
  ]
  const create: Command[] = canWrite
    ? [
        {
          key: 'new',
          label: 'Add a new entry',
          icon: 'plus',
          run: () => {
            onClose()
            onNewEntry()
          },
        },
      ]
    : []

  const lower = term.toLowerCase()
  const found = term.length >= 2 ? (search.data?.items ?? []) : entries.items.slice(0, 6)
  const entryCommands: Command[] = found
    .filter((item) => item.lifecycle === 'active' || term.length >= 2)
    .map((item) => ({
      key: item.id,
      label: item.name,
      meta: typeLabel(item.type) + (item.lifecycle === 'active' ? '' : ' · archived'),
      type: item.type,
      run: go(entryPath(scope, item.id)),
    }))
  const pageCommands = pages.filter((page) => !lower || page.label.toLowerCase().includes(lower))
  const commands = [...entryCommands, ...pageCommands, ...create]
  const active = Math.min(index, Math.max(commands.length - 1, 0))

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setIndex((active + 1) % Math.max(commands.length, 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setIndex((active - 1 + commands.length) % Math.max(commands.length, 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      commands[active]?.run()
    }
  }

  const option = (command: Command, position: number) => (
    <li key={command.key} role="presentation">
      <button
        type="button"
        role="option"
        id={`palette-${position}`}
        aria-selected={position === active}
        className="palette-item"
        tabIndex={-1}
        onMouseEnter={() => setIndex(position)}
        onClick={command.run}
      >
        {command.type ? (
          <TypeAvatar type={command.type} name={command.label} size="sm" />
        ) : (
          <Icon name={command.icon ?? 'arrowRight'} />
        )}
        <span>{command.label}</span>
        {command.meta && <span className="meta">{command.meta}</span>}
      </button>
    </li>
  )

  return (
    <Dialog onClose={onClose} className="palette" labelledBy="palette-label">
      <div className="palette-input">
        <Icon name="search" />
        <label id="palette-label" className="sr-only" htmlFor="palette-search">
          Search your story bible
        </label>
        <input
          id="palette-search"
          data-autofocus
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={commands.length > 0 ? `palette-${active}` : undefined}
          autoComplete="off"
          placeholder="Search entries, or jump to a page…"
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setIndex(0)
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      <ul className="palette-list" id="palette-list" role="listbox" aria-label="Results">
        {entryCommands.length > 0 && (
          <li className="palette-label eyebrow" role="presentation">
            {term.length >= 2 ? 'Entries' : 'Jump to an entry'}
          </li>
        )}
        {entryCommands.map((command, position) => option(command, position))}
        {term.length >= 2 && search.isSuccess && entryCommands.length === 0 && (
          <li className="palette-label muted" role="presentation">
            No entry matches “{term}”.
          </li>
        )}
        {pageCommands.length + create.length > 0 && (
          <li className="palette-label eyebrow" role="presentation">
            Go to
          </li>
        )}
        {[...pageCommands, ...create].map((command, position) =>
          option(command, entryCommands.length + position),
        )}
      </ul>
    </Dialog>
  )
}
