import { fireEvent, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { mockApi, renderInProject } from '../test/harness'
import { MentionTextarea } from './MentionTextarea'
import { Dialog } from './ui'

const mill = entity({ id: 'ent_mill', name: 'Mill', type: 'Location', aliases: [], attributes: {} })

function Box({ start = '' }: { start?: string }) {
  const [value, setValue] = useState(start)
  return <MentionTextarea aria-label="Words" value={value} onValueChange={setValue} />
}

/** Puts the caret at a place in the box, as a click or an arrow key would. */
function caretAt(box: HTMLTextAreaElement, position: number) {
  box.focus()
  box.setSelectionRange(position, position)
  fireEvent.select(box)
}

/** Types into the box the way a browser reports it: the new words, then the caret after them. */
function typeIn(box: HTMLTextAreaElement, value: string) {
  box.focus()
  fireEvent.change(box, { target: { value, selectionStart: value.length, selectionEnd: value.length } })
}

const original = document.execCommand

afterEach(() => {
  vi.unstubAllGlobals()
  document.execCommand = original
})

describe('MentionTextarea', () => {
  it('stays closed after a name is put in, even when the browser reports the change straight away', async () => {
    mockApi({ 'GET /entities': { items: [mill], total: 1 } })
    // A browser's own insertText fires the change at once, inside the pick.
    document.execCommand = ((command: string, _ui?: boolean, text?: string) => {
      const box = document.activeElement as HTMLTextAreaElement
      if (command !== 'insertText' || !text) return false
      const from = box.selectionStart
      const next = box.value.slice(0, from) + text + box.value.slice(box.selectionEnd)
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(box, next)
      box.setSelectionRange(from + text.length, from + text.length)
      box.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    }) as typeof document.execCommand
    renderInProject(<Box start="Then @Mi." />)
    const box = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Words' })
    caretAt(box, 8)
    await screen.findByRole('option', { name: /Mill/ })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(box.value).toBe('Then @Mill.')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('leaves keys alone while an input method is composing, as Safari reports it', async () => {
    mockApi({ 'GET /entities': { items: [mill], total: 1 } })
    renderInProject(<Box />)
    const box = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Words' })
    typeIn(box, 'Then @Mi')
    await screen.findByRole('option', { name: /Mill/ })
    fireEvent.keyDown(box, { key: 'Enter', keyCode: 229 })
    expect(box.value).toBe('Then @Mi')
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('keeps Tab inside a dialog when the list has no names to offer, and takes it when it has', async () => {
    mockApi({ 'GET /entities': { items: [mill], total: 1 } })
    const onClose = vi.fn()
    renderInProject(
      <Dialog title="Notes" onClose={onClose}>
        <Box />
      </Dialog>,
    )
    const close = screen.getByRole('button', { name: /Close/ })
    const box = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Words' })

    // Names showing: Tab puts one in and focus stays in the box.
    typeIn(box, 'Then @Mi')
    await screen.findByRole('option', { name: /Mill/ })
    fireEvent.keyDown(box, { key: 'Tab' })
    expect(box.value).toBe('Then @Mill ')
    expect(box).toHaveFocus()

    // No names match: the list is open but empty, so Tab moves on round the dialog.
    typeIn(box, 'Then @Zz')
    expect(await screen.findByText(/No entry is called/, { selector: 'p' })).toBeInTheDocument()
    fireEvent.keyDown(box, { key: 'Tab' })
    expect(close).toHaveFocus()

    // Escape closes the list, not the dialog.
    typeIn(box, 'Then @Zz')
    await screen.findByText(/No entry is called/, { selector: 'p' })
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.queryByText(/No entry is called/, { selector: 'p' })).not.toBeInTheDocument()
  })
})
