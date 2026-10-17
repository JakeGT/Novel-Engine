import { afterEach, describe, expect, it } from 'vitest'
import { browserOffset, excerpt, forgetUnsaved, keepUnsaved, locate, orderChecks, readUnsaved } from './studio'

afterEach(() => window.localStorage.clear())

describe('studio helpers', () => {
  it('turns a server position into a browser one, even past an emoji', () => {
    expect(browserOffset('plain words', 5)).toBe(5)
    // "🌊" is one character to the server and two to the browser.
    const text = 'Sea 🌊 and sky'
    expect(text.slice(browserOffset(text, 6), browserOffset(text, 9))).toBe('and')
  })

  it('finds a passage again after the text around it changed, or says it is gone', () => {
    const saved = 'The lamp was lit. Tobin waited.'
    const start = saved.indexOf('Tobin')
    expect(locate(saved, saved, start, start + 5)).toEqual({ start, end: start + 5 })
    const edited = 'At dusk the lamp was lit. Tobin waited.'
    expect(locate(saved, edited, start, start + 5)).toEqual({ start: edited.indexOf('Tobin'), end: edited.indexOf('Tobin') + 5 })
    expect(locate(saved, 'The lamp was lit. Nobody waited.', start, start + 5)).toBeNull()
  })

  it('quotes a long passage short, at a word', () => {
    const text = 'one two three four five six seven eight nine ten'
    expect(excerpt(text, { start: 0, end: text.length }, 20)).toBe('one two three four…')
  })

  it('puts warnings before notes, then in reading order', () => {
    const ordered = orderChecks([
      { code: 'a', severity: 'note', message: 'n', start: 1, end: 2 },
      { code: 'b', severity: 'warning', message: 'w2', start: 9, end: 10 },
      { code: 'c', severity: 'warning', message: 'w1', start: 3, end: 4 },
    ])
    expect(ordered.map((check) => check.code)).toEqual(['c', 'b', 'a'])
  })

  it('keeps unsaved words per scene and line of the story', () => {
    const story = { workspaceId: 'ws', projectId: 'prj' }
    const whatIf = { ...story, branchId: 'brn_1' }
    keepUnsaved(story, 'pln_1', { text: 'Mine', base: 'ver_1' })
    expect(readUnsaved(story, 'pln_1')).toEqual({ text: 'Mine', base: 'ver_1' })
    expect(readUnsaved(whatIf, 'pln_1')).toBeNull()
    forgetUnsaved(story, 'pln_1')
    expect(readUnsaved(story, 'pln_1')).toBeNull()
  })
})
