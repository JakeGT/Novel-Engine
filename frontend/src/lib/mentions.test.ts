import { describe, expect, it } from 'vitest'
import { fold, formatMention, insertMention, mentionAt, suggest } from './mentions'

const entries = [
  { id: 'ent_ines', name: 'Inés Varga', type: 'Character', aliases: ['the Keeper'], lifecycle: 'active' },
  { id: 'ent_tobin', name: 'Tobin Ashe', type: 'Character', aliases: [], lifecycle: 'active', attributes: { _importance: 'main' } },
  { id: 'ent_point', name: 'Kestrel Point', type: 'Location', aliases: ['the Point'], lifecycle: 'active' },
  { id: 'ent_old', name: 'Ines the Elder', type: 'Character', aliases: [], lifecycle: 'retired' },
  { id: 'ent_mill', name: 'Mill', type: 'Location', aliases: [], lifecycle: 'active' },
]

describe('the mention being typed', () => {
  it('starts at an @ that does not follow a word, and runs to the caret', () => {
    expect(mentionAt('She saw @Ke', 11)).toEqual({ start: 8, end: 11, text: 'Ke', quoted: false })
    expect(mentionAt('She saw @', 9)).toEqual({ start: 8, end: 9, text: '', quoted: false })
    // An address is not a mention, nor is an @ after another @.
    expect(mentionAt('mail jake@example', 17)).toBeNull()
    expect(mentionAt('@@Ke', 4)).toBeNull()
    // A space ends a bare mention; a quoted one may hold spaces until the quote closes.
    expect(mentionAt('@Kestrel P', 10)).toBeNull()
    expect(mentionAt('see @"Kestrel P', 15)).toEqual({ start: 4, end: 15, text: 'Kestrel P', quoted: true })
    expect(mentionAt('@"two\nlines', 11)).toBeNull()
  })

  it('takes in the rest of a word or a quoted name the caret is inside, and matches on all of it', () => {
    expect(mentionAt('@Kestrel, then', 3)).toEqual({ start: 0, end: 8, text: 'Kestrel', quoted: false })
    expect(mentionAt('@"Kestrel Point" said', 4)).toEqual({ start: 0, end: 16, text: 'Kestrel Point', quoted: true })
    expect(mentionAt('@"Kestrel Point" said', 9)).toEqual({ start: 0, end: 16, text: 'Kestrel Point', quoted: true })
    expect(mentionAt('@"Kes said', 4)).toEqual({ start: 0, end: 5, text: 'Kes', quoted: true })
  })

  it('never stretches a quoted name to a quotation mark that opens other words', () => {
    const text = 'Then @"Kes He said "run."'
    const query = mentionAt(text, 10)!
    expect(query).toEqual({ start: 5, end: 10, text: 'Kes', quoted: true })
    expect(insertMention(text, query, 'Kestrel Point').value).toBe('Then @"Kestrel Point" He said "run."')
    // Nor to a closing quote after sentence punctuation, as at the end of speech.
    expect(mentionAt('"Wait, @"Kes, run," she said', 12)?.end).toBe(12)
  })

  it('does not write over a word the @ was typed in front of', () => {
    // Typing @ before "Mara" matches on "Mara": choosing her puts the mark on her name.
    const query = mentionAt('She saw @Mara today', 9)!
    expect(query).toEqual({ start: 8, end: 13, text: 'Mara', quoted: false })
    expect(suggest(entries, query.text)).toEqual([])
    expect(insertMention('She saw @Mara today', query, 'Mara').value).toBe('She saw @Mara today')
    // A letter typed after the @ is matched together with the word, so no other name fits it.
    expect(mentionAt('She saw @TMara today', 10)?.text).toBe('TMara')
    expect(suggest(entries, 'TMara')).toEqual([])
  })
})

describe('suggestions', () => {
  it('matches names and aliases without minding case or accents, best first', () => {
    expect(fold('  Inés   VARGA ')).toBe('ines varga')
    expect(suggest(entries, 'ines').map((option) => option.id)).toEqual(['ent_ines'])
    expect(suggest(entries, 'point').map((option) => [option.id, option.label])).toEqual([['ent_point', 'Kestrel Point']])
    const keeper = suggest(entries, 'keep')
    expect(keeper).toEqual([{ id: 'ent_ines', name: 'Inés Varga', type: 'Character', label: 'the Keeper', via: 'the Keeper' }])
    // Retired entries are not offered.
    expect(suggest(entries, 'elder')).toEqual([])
  })

  it('offers the most central entries first when nothing is typed yet', () => {
    expect(suggest(entries, '')[0].id).toBe('ent_tobin')
    expect(suggest(entries, '', 2)).toHaveLength(2)
  })
})

describe('writing the mark', () => {
  it('uses the marking the name finder reads', () => {
    expect(formatMention('Mill')).toBe('@Mill')
    expect(formatMention("O'Neill")).toBe("@O'Neill")
    expect(formatMention('Kestrel Point')).toBe('@"Kestrel Point"')
    expect(formatMention('2nd Watch')).toBe('@"2nd Watch"')
    expect(formatMention('The "Fox"')).toBe('[[The "Fox"]]')
  })

  it('replaces what was typed and leaves the caret after the name and a space', () => {
    const typed = 'She saw @Ke at dusk.'
    const query = mentionAt(typed, 11)!
    expect(insertMention(typed, query, 'Kestrel Point')).toEqual({
      value: 'She saw @"Kestrel Point" at dusk.',
      caret: 24,
      inserted: '@"Kestrel Point"',
    })
    expect(insertMention('Then @Mi', { start: 5, end: 8 }, 'Mill')).toEqual({
      value: 'Then @Mill ',
      caret: 11,
      inserted: '@Mill ',
    })
    expect(insertMention('@Mi.', { start: 0, end: 3 }, 'Mill').value).toBe('@Mill.')
  })
})
