import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { clientConfig } from '../lib/accessKeys'
import { AccessKeys } from './AccessKeys'

afterEach(() => {
  vi.unstubAllGlobals()
})

const KEY = 'nvlpat_0123456789abcdefghijABCDEFGHIJ0123456789abcdefgh'
const now = new Date().toISOString()
const later = new Date(Date.now() + 86_400_000 * 90).toISOString()

function accessKey(overrides: Record<string, unknown> = {}) {
  return {
    id: 'key_1',
    name: 'Claude on my laptop',
    hint: 'efgh',
    project_id: 'prj_1',
    may_suggest: false,
    status: 'active',
    revoked_reason: null,
    created_at: now,
    expires_at: later,
    last_used_at: null,
    revoked_at: null,
    mine: true,
    holder_name: null,
    ...overrides,
  }
}

function show(props: Partial<Parameters<typeof AccessKeys>[0]> = {}) {
  return renderInProject(
    <AccessKeys workspaceId="ws_1" projectId="prj_1" canWrite hosted {...props} />,
  )
}

describe('AccessKeys', () => {
  it('makes a read-only key for this story by default and shows it once with settings', async () => {
    let made = false
    const api = mockApi({
      'GET /access-keys': () => (made ? [accessKey()] : []),
      'GET /projects': [{ id: 'prj_1', name: 'The Lighthouse', slug: 'l', workspace_id: 'ws_1' }],
      'POST /access-keys': () => {
        made = true
        return { access_key: accessKey(), key: KEY }
      },
    })
    const user = userEvent.setup()
    const { container } = show()

    expect(await screen.findByText('No keys yet.')).toBeInTheDocument()
    expect(await screen.findByLabelText('Only this story (The Lighthouse)')).toBeChecked()
    expect(screen.getByLabelText('Read only')).toBeChecked()
    expect(screen.getByLabelText('Works for')).toHaveValue('90')
    await expectAccessible(container)

    const name = screen.getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Claude on my laptop')
    await user.click(screen.getByRole('button', { name: 'Make access key' }))

    await waitFor(() => expect(api.find('POST', '/access-keys')).toHaveLength(1))
    expect(api.find('POST', '/access-keys')[0].body).toEqual({
      name: 'Claude on my laptop',
      project_id: 'prj_1',
      may_suggest: false,
      expires_in_days: 90,
    })
    const shown = (await screen.findByText('Copy this key now.')).closest('.notice') as HTMLElement
    expect(shown).toHaveTextContent('shown only once')
    expect(within(shown).getByLabelText('Access key')).toHaveValue(KEY)
    const settings = within(shown).getByLabelText('Settings for your assistant') as HTMLTextAreaElement
    expect(settings.value).toContain(`"Authorization": "Bearer ${KEY}"`)
    expect(settings.value).toContain('/mcp')
    // The list shows the key's ending, never the key.
    const rows = await screen.findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('ends in …efgh')
    expect(rows[0]).not.toHaveTextContent(KEY)
    await expectAccessible(container)
  })

  it('says what is missing next to the name instead of sending nothing', async () => {
    const api = mockApi({ 'GET /access-keys': [], 'GET /projects': [] })
    const user = userEvent.setup()
    show()
    const name = await screen.findByLabelText('Name')
    await user.clear(name)
    await user.click(screen.getByRole('button', { name: 'Make access key' }))
    expect(await screen.findByText('Give the key a name.')).toBeInTheDocument()
    expect(api.find('POST', '/access-keys')).toHaveLength(0)
  })

  it('lets only someone who can change the story allow suggestions', async () => {
    mockApi({ 'GET /access-keys': [], 'GET /projects': [] })
    show({ canWrite: false })
    const suggest = await screen.findByLabelText(/Read and suggest changes/)
    expect(suggest).toBeDisabled()
    expect(screen.getByText('Only someone who can change the story can allow this.')).toBeInTheDocument()
  })

  it('lists keys with their state and use, and asks before withdrawing one', async () => {
    const api = mockApi({
      'GET /access-keys': [
        accessKey({ last_used_at: now, may_suggest: true }),
        accessKey({ id: 'key_2', name: 'Old one', status: 'revoked', revoked_reason: 'access_lost' }),
        accessKey({ id: 'key_3', name: 'Sam’s', mine: false, holder_name: 'Sam Reed', project_id: null }),
      ],
      'GET /projects': [],
      'GET /access-keys/key_1/uses': [
        { day: '2026-10-18', tool: 'read_entry', calls: 4, refused: 1 },
        { day: '2026-10-17', tool: 'read_entry', calls: 2, refused: 0 },
      ],
      'POST /access-keys/key_1/revoke': accessKey({ status: 'revoked', revoked_reason: 'holder' }),
    })
    const user = userEvent.setup()
    const { container } = show()

    const rows = await screen.findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('reads and suggests')
    expect(rows[0]).toHaveTextContent('Last used just now')
    expect(rows[1]).toHaveTextContent('Stopped: its holder no longer has access')
    expect(within(rows[1]).queryByRole('button', { name: /Withdraw/ })).not.toBeInTheDocument()
    expect(rows[2]).toHaveTextContent('Sam Reed’s')
    expect(rows[2]).toHaveTextContent('Every story you can read here')

    await user.click(within(rows[0]).getByRole('button', { name: 'Show use of Claude on my laptop' }))
    const table = await within(rows[0]).findByRole('table')
    expect(table).toHaveTextContent('Read entries')
    expect(table).toHaveTextContent('6')
    await expectAccessible(container)

    await user.click(within(rows[0]).getByRole('button', { name: 'Withdraw Claude on my laptop' }))
    const dialog = screen.getByRole('dialog', { name: 'Withdraw “Claude on my laptop”?' })
    expect(dialog).toHaveTextContent('cannot be undone')
    expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw key' }))
    await waitFor(() => expect(api.find('POST', '/access-keys/key_1/revoke')).toHaveLength(1))
    expect(await screen.findByText('“Claude on my laptop” no longer works.')).toBeInTheDocument()
  })

  it('writes settings most assistants read', () => {
    const settings = JSON.parse(clientConfig(KEY, 'https://example.test/mcp'))
    expect(settings).toEqual({
      mcpServers: {
        'novel-engine': {
          type: 'http',
          url: 'https://example.test/mcp',
          headers: { Authorization: `Bearer ${KEY}` },
        },
      },
    })
  })
})
