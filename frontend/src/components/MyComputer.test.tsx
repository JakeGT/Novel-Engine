import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, renderInProject, reply } from '../test/harness'
import { MyComputerCard } from './MyComputer'

afterEach(() => {
  vi.unstubAllGlobals()
})

const now = new Date().toISOString()

function computer(overrides: Record<string, unknown> = {}) {
  return {
    id: 'dev_1',
    name: 'Jake’s laptop',
    status: 'connected',
    online: true,
    last_seen_at: now,
    connected_at: now,
    connector_version: '1.0.0',
    up_to_date: true,
    server: 'Ollama',
    models: [
      { name: 'llama3.2:3b', context_tokens: 8192 },
      { name: 'qwen3:8b', context_tokens: null },
    ],
    structured_output: true,
    in_use: false,
    disconnected_because: null,
    disconnected_at: null,
    ...overrides,
  }
}

const CODE = { name: 'Jake’s laptop', connector_version: '1.0.0', asked_at: now, expires_at: now, status: 'pending' }

describe('MyComputerCard', () => {
  it('checks a code, shows what it connects, and connects it only when confirmed', async () => {
    let connected = false
    const api = mockApi({
      'GET /ai/connector/devices': () => (connected ? [computer({ models: [] })] : []),
      'POST /ai/connector/codes/check': CODE,
      'POST /ai/connector/codes/decide': () => {
        connected = true
        return { ...CODE, status: 'approved' }
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<MyComputerCard workspaceId="ws_1" />)

    expect(await screen.findByText('No computer is connected.')).toBeInTheDocument()
    expect(screen.getByText(/python -m connector --site/)).toBeInTheDocument()
    await expectAccessible(container)

    const field = screen.getByLabelText('Code from the connector')
    await user.type(field, 'bcd')
    await user.click(screen.getByRole('button', { name: 'Check code' }))
    expect(await screen.findByText('The code has eight letters, like BCDF-GHJK.')).toBeInTheDocument()
    expect(api.find('POST', '/codes/check')).toHaveLength(0)
    // What was typed is kept.
    expect(field).toHaveValue('BCD')

    await user.type(field, 'f-ghjk')
    await user.click(screen.getByRole('button', { name: 'Check code' }))
    const confirm = await screen.findByRole('group', { name: 'Confirm the computer' })
    expect(within(confirm).getByText('Connect “Jake’s laptop”?')).toBeInTheDocument()
    expect(within(confirm).getByText(/If someone sent you this code, turn it down/)).toBeInTheDocument()
    expect(api.find('POST', '/codes/decide')).toHaveLength(0)
    await expectAccessible(container)

    await user.click(within(confirm).getByRole('button', { name: 'Connect this computer' }))
    await waitFor(() => expect(api.find('POST', '/codes/decide')).toHaveLength(1))
    expect(api.find('POST', '/codes/decide')[0].body).toEqual({ code: 'BCDF-GHJK', approve: true })
    expect(await screen.findByText(/Connected “Jake’s laptop”/)).toBeInTheDocument()
    expect(await screen.findByText(/no models listed yet/)).toBeInTheDocument()
  })

  it('a code can be turned down', async () => {
    const api = mockApi({
      'GET /ai/connector/devices': [],
      'POST /ai/connector/codes/check': CODE,
      'POST /ai/connector/codes/decide': { ...CODE, status: 'denied' },
    })
    const user = userEvent.setup()
    renderInProject(<MyComputerCard workspaceId="ws_1" />)
    await user.type(await screen.findByLabelText('Code from the connector'), 'BCDFGHJK')
    await user.click(screen.getByRole('button', { name: 'Check code' }))
    await user.click(await screen.findByRole('button', { name: 'Turn it down' }))
    await waitFor(() => expect(api.find('POST', '/codes/decide')[0]?.body).toEqual({ code: 'BCDFGHJK', approve: false }))
    expect(screen.queryByText(/Connected “/)).not.toBeInTheDocument()
  })

  it('says plainly when a code ran out', async () => {
    mockApi({
      'GET /ai/connector/devices': [],
      'POST /ai/connector/codes/check': reply(404, {
        code: 'not_found',
        detail: 'That code was not found, or it ran out.',
      }),
    })
    const user = userEvent.setup()
    renderInProject(<MyComputerCard workspaceId="ws_1" />)
    await user.type(await screen.findByLabelText('Code from the connector'), 'BCDFGHJK')
    await user.click(screen.getByRole('button', { name: 'Check code' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('it ran out')
  })

  it('uses a computer’s model for the assistant, and disconnects it after saying what stops', async () => {
    let inUse = false
    let gone = false
    const api = mockApi({
      'GET /ai/connector/devices': () =>
        gone
          ? [computer({ status: 'disconnected', online: false, disconnected_because: 'owner', disconnected_at: now })]
          : [computer({ in_use: inUse })],
      'POST /devices/dev_1/use': () => {
        inUse = true
        return computer({ in_use: true })
      },
      'POST /devices/dev_1/disconnect': () => {
        gone = true
        return computer({ status: 'disconnected' })
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<MyComputerCard workspaceId="ws_1" />)

    const model = await screen.findByLabelText('Model on “Jake’s laptop”')
    expect(model).toHaveValue('llama3.2:3b')
    expect(screen.getByText('It was given room for 8,192 tokens.')).toBeInTheDocument()
    await user.selectOptions(model, 'qwen3:8b')
    expect(screen.getByText('Its server did not say how much it can read at once.')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(screen.getByRole('button', { name: 'Use for the assistant' }))
    await waitFor(() => expect(api.find('POST', '/devices/dev_1/use')).toHaveLength(1))
    expect(api.find('POST', '/devices/dev_1/use')[0].body).toEqual({ model: 'qwen3:8b' })
    expect(await screen.findByText('In use')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Disconnect “Jake’s laptop”' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/does not move to a key on its own/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Disconnect computer' }))
    await waitFor(() => expect(api.find('POST', '/devices/dev_1/disconnect')).toHaveLength(1))
    expect(await screen.findByText('Disconnected computers (1)')).toBeInTheDocument()
    expect(screen.getByText('No computer is connected.')).toBeInTheDocument()
  })

  it('says when a connector is not running or too old', async () => {
    mockApi({
      'GET /ai/connector/devices': [computer({ online: false, up_to_date: false, connector_version: '0.9.0' })],
    })
    renderInProject(<MyComputerCard workspaceId="ws_1" />)
    expect(await screen.findByText('Not running')).toBeInTheDocument()
    expect(screen.getByText(/This connector is too old for this site/)).toBeInTheDocument()
  })

  it('tells a member who is not an owner who can connect a computer', async () => {
    mockApi({
      'GET /ai/connector/devices': reply(403, { code: 'forbidden', detail: 'You do not have permission.' }),
    })
    renderInProject(<MyComputerCard workspaceId="ws_1" />)
    expect(await screen.findByText('Only a workspace owner can connect a computer.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Code from the connector')).not.toBeInTheDocument()
  })
})
