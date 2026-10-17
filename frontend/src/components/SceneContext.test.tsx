import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { SceneContextDialog } from './SceneContext'

afterEach(() => vi.unstubAllGlobals())

const scene = {
  id: 'plan_1',
  kind: 'scene',
  title: 'The Docks',
  summary: '',
  parent_id: null,
  position: 0,
  refs: [],
  sets_up: [],
  pays_off: [],
  details: {},
  conditions: [],
  created_revision: 2,
}

const item = (key: string, section: string, title: string, text: string, reason: string, hard = false) => ({
  key,
  section,
  section_title: title,
  text,
  reason,
  authority: 'fact',
  record_type: 'fact',
  record_id: key,
  location: null,
  words: text.split(' ').length,
  score: 1,
  hard,
  pinned: false,
})

const context = {
  scene_id: 'plan_1',
  point_of_view: 'ent_mara',
  settings: { pins: [], excludes: [], budget: null, include_secrets: false },
  policy_version: 'scene-draft/1',
  budget: 2500,
  words: 12,
  checksum: 'abcdef0123456789',
  items: [
    item('fact:1', 'true', 'What is true in the story', 'Mara is located at Lantern Harbor.', 'It is about Mara, who is in the scene.'),
    item('scene:plan_1', 'scene', 'The scene', 'The Docks.', 'This is the scene to write.', true),
  ],
  left_out: [
    {
      reason: 'secret',
      item: item('fact:2', 'secrets', 'For the author only', 'Mara is a member of the guild.', 'It is a secret.'),
    },
  ],
  index: { texts: 0, indexed: 0, behind: [], names_as_of: null, semantic: false },
  rendered: '## The scene\n- The Docks.\n',
}

describe('SceneContextDialog', () => {
  it('lists what would be given with why, and leaves out on request', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity()], limit: 200, offset: 0 },
      'GET /plan/plan_1/context': context,
      'PUT /plan/plan_1/context': context,
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SceneContextDialog scene={scene as never} onClose={vi.fn()} />)
    const dialog = await screen.findByRole('dialog', { name: 'What the assistant would use for “The Docks”' })
    expect(await within(dialog).findByText(/About 12 words/)).toBeInTheDocument()
    expect(within(dialog).getByText(/It is about Mara, who is in the scene/)).toBeInTheDocument()
    expect(within(dialog).getByText('Always given')).toBeInTheDocument()
    expect(within(dialog).getByText('Secrets the viewpoint character does not know')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(within(dialog).getByRole('button', { name: 'Don’t use' }))
    await waitFor(() => expect(api.find('PUT', '/plan/plan_1/context')).toHaveLength(1))
    expect(api.find('PUT', '/plan/plan_1/context')[0].body).toEqual({
      pins: [],
      excludes: ['fact:1'],
      budget: null,
      include_secrets: false,
      expected_revision: 3,
    })
  })
})
