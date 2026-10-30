import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlanItem, ScaleProgress } from '../api/client'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { StoryScales } from './StoryScales'

afterEach(() => vi.unstubAllGlobals())

const trust = {
  id: 'trust',
  name: 'Trust',
  levels: ['None', 'Wary', 'Some', 'Complete'],
  ordered: true,
  one_step: true,
  about: 'How far they trust the crew.',
}
const progress: ScaleProgress[] = [
  {
    scale: trust,
    rows: [
      {
        arc_id: 'pln_arc',
        about: ['ent_mara'],
        start: 'None',
        target: 'Complete',
        steps: [
          { scene_id: 'pln_a', scene_number: 1, level: 'Some', toward: 'toward' },
          { scene_id: 'pln_b', scene_number: 2, level: 'Wary', toward: 'away' },
          { scene_id: 'pln_c', scene_number: 4, level: 'Complete', toward: 'reached' },
        ],
      },
    ],
  },
]
const byId = new Map<string, PlanItem>()
const base = { 'GET /entities': { items: [entity()], limit: 200, offset: 0 } }

describe('StoryScales', () => {
  it('shows where each character stands, scene by scene, and which way each move goes', async () => {
    mockApi({ ...base, 'GET /plan/scales': { scales: [trust], version: 2 } })
    const { container } = renderInProject(<StoryScales progress={progress} byId={byId} />)

    const table = await screen.findByRole('table', { name: 'Where each character stands on Trust' })
    const [, row] = within(table).getAllByRole('row')
    expect(row).toHaveTextContent('Mara Venn')
    expect(row).toHaveTextContent('Scene 1: Some Closer to where the arc ends')
    expect(row).toHaveTextContent('Scene 2: Wary Further from where the arc ends')
    expect(row).toHaveTextContent('Scene 4: Complete Where the arc ends')
    expect(screen.getByText(/In order: None → Wary → Some → Complete · one level at a time/)).toBeInTheDocument()
    await expectAccessible(container)
  })

  it('adds a scale, with its levels one per line, and says what is missing first', async () => {
    const api = mockApi({
      ...base,
      'GET /plan/scales': { scales: [], version: 0 },
      'PUT /plan/scales': { scales: [trust], version: 1 },
    })
    const user = userEvent.setup()
    renderInProject(<StoryScales progress={[]} byId={byId} />)

    expect(await screen.findByText(/No scales yet/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add a scale' }))
    const dialog = screen.getByRole('dialog', { name: 'Scales your characters share' })
    await user.click(within(dialog).getByRole('button', { name: 'Save the scales' }))
    expect(within(dialog).getByText('Name this scale, or remove it.')).toBeInTheDocument()
    expect(api.find('PUT', '/plan/scales')).toHaveLength(0)

    await user.type(within(dialog).getByLabelText('Name'), 'Trust')
    await user.type(within(dialog).getByLabelText(/Levels, one per line/), 'None')
    await user.click(within(dialog).getByRole('button', { name: 'Save the scales' }))
    expect(within(dialog).getByText('Give it at least two levels, one per line.')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(/Levels, one per line/), '{Enter}Complete')
    await user.click(within(dialog).getByLabelText('A character moves one level at a time'))
    await user.click(within(dialog).getByRole('button', { name: 'Save the scales' }))

    await waitFor(() => expect(api.find('PUT', '/plan/scales')).toHaveLength(1))
    expect(api.find('PUT', '/plan/scales')[0].body).toEqual({
      expected_version: 0,
      scales: [{ id: 'trust', name: 'Trust', levels: ['None', 'Complete'], ordered: true, one_step: true, about: '' }],
    })
  })

  it('offers no changes to someone who can only read', async () => {
    mockApi({ ...base, 'GET /plan/scales': { scales: [trust], version: 2 } })
    renderInProject(<StoryScales progress={progress} byId={byId} />, { context: { access: VIEWER } })
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change the scales' })).not.toBeInTheDocument()
  })
})
