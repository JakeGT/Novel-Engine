import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { SearchWhereCard } from './SearchWhereCard'

afterEach(() => vi.unstubAllGlobals())

describe('SearchWhereCard', () => {
  it('names the service writing is sent to, and says it is not the author’s key', async () => {
    mockApi({ 'GET /ai/search': { kind: 'service', name: 'OpenAI', host: 'api.openai.com', in_use: true } })
    const { container } = renderInProject(<SearchWhereCard />)
    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('This site sends your writing to OpenAI (api.openai.com) to make it searchable.')
    expect(note).toHaveTextContent('not your key')
    expect(note).not.toHaveTextContent('not switched on yet')
    await expectAccessible(container)
  })

  it('says when it is set up but not switched on yet', async () => {
    mockApi({ 'GET /ai/search': { kind: 'service', name: null, host: 'embed.example.com', in_use: false } })
    renderInProject(<SearchWhereCard />)
    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('sends your writing to embed.example.com to make it searchable')
    expect(note).toHaveTextContent('not switched on yet, so for now passages are found by the words they use')
  })

  it('says writing stays on the server when the model runs there', async () => {
    mockApi({ 'GET /ai/search': { kind: 'server', name: null, host: null, in_use: true } })
    renderInProject(<SearchWhereCard />)
    expect(await screen.findByRole('note')).toHaveTextContent('Your writing is not sent anywhere for it.')
  })

  it('says when search by meaning is off', async () => {
    mockApi({ 'GET /ai/search': { kind: 'off', name: null, host: null, in_use: false } })
    renderInProject(<SearchWhereCard />)
    expect(await screen.findByRole('note')).toHaveTextContent('Search by meaning is off on this site')
  })
})
