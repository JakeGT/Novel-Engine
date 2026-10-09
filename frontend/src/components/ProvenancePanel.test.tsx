import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { provenanceEntry } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject, VIEWER } from '../test/harness'
import { ProvenancePanel } from './ProvenancePanel'

afterEach(() => vi.unstubAllGlobals())

describe('ProvenancePanel', () => {
  it('shows who decided and why to members who may see it', async () => {
    mockApi({ 'GET /provenance/entity/ent_mara': { items: [provenanceEntry()] } })
    renderInProject(<ProvenancePanel recordType="entity" recordId="ent_mara" />)

    expect(await screen.findByText(/by usr_editor — checked against the outline/)).toBeInTheDocument()
    expect(screen.getByText('scene:ch01')).toBeInTheDocument()
    expect(screen.queryByText(/hidden for your role/)).not.toBeInTheDocument()
  })

  it('says plainly when reviewer identity and notes are withheld', async () => {
    mockApi({
      'GET /provenance/entity/ent_mara': {
        items: [
          provenanceEntry({
            redacted: true,
            actor_user_id: null,
            decisions: [
              { decision: 'approved', actor_user_id: null, note: null, created_at: '2026-10-09T10:00:00Z' },
            ],
          }),
        ],
      },
    })
    const { container } = renderInProject(
      <ProvenancePanel recordType="entity" recordId="ent_mara" />,
      { context: { access: VIEWER } },
    )

    expect(await screen.findByText(/Reviewer names and review notes are hidden for your role/)).toBeInTheDocument()
    expect(screen.getByText(/Review: approved \(reviewer and note hidden\)/)).toBeInTheDocument()
    expect(screen.getByText(/author hidden/)).toBeInTheDocument()
    // Origin and evidence remain visible: why canon is this way is not secret.
    expect(screen.getByText(/origin model/)).toBeInTheDocument()
    expect(screen.getByText('scene:ch01')).toBeInTheDocument()
    await expectAccessible(container)
  })
})
