import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { provenanceEntry } from '../test/fixtures'
import { expectAccessible, mockApi, renderInProject } from '../test/harness'
import { ActivityPanel } from './ActivityPanel'

afterEach(() => vi.unstubAllGlobals())

describe('ActivityPanel', () => {
  it('says where each change came from and how it was reviewed, without account ids', async () => {
    mockApi({
      'GET /provenance/entity/ent_mara': {
        items: [
          provenanceEntry(),
          provenanceEntry({
            revision: 2,
            commit_id: 'cmt_2',
            operation_type: 'update_entity',
            origin: 'author',
            actor_user_id: 'usr_me',
            evidence: [],
            decisions: [{ decision: 'approved', actor_user_id: 'usr_me', note: 'author-direct', created_at: '2026-10-09T10:00:00Z' }],
          }),
        ],
      },
    })
    const { container } = renderInProject(<ActivityPanel recordType="entity" recordId="ent_mara" />, {
      context: { userId: 'usr_me' },
    })

    const items = await screen.findAllByRole('listitem')
    // Newest first.
    expect(items[0]).toHaveTextContent('Edited by you')
    expect(items[0]).toHaveTextContent('Version 2')
    // An author's own automatic approval is not listed as a review.
    expect(items[0]).not.toHaveTextContent('Reviewed')
    expect(items[1]).toHaveTextContent('Added from the AI assistant')
    expect(items[1]).toHaveTextContent('Reviewed: approved by a collaborator — “checked against the outline”')
    expect(items[1]).toHaveTextContent('scene:ch01 at chars:10-40')
    expect(container).not.toHaveTextContent('usr_')
    await expectAccessible(container)
  })

  it('says so when reviewer details are hidden for the reader', async () => {
    mockApi({
      'GET /provenance/entity/ent_mara': {
        items: [provenanceEntry({ redacted: true, actor_user_id: null, decisions: [{ decision: 'approved', actor_user_id: null, note: null, created_at: '2026-10-09T10:00:00Z' }] })],
      },
    })
    renderInProject(<ActivityPanel recordType="entity" recordId="ent_mara" />)

    expect(await screen.findByRole('note')).toHaveTextContent('Reviewer names and notes are hidden for your role')
    expect(screen.getByText(/reviewer and note hidden/)).toBeInTheDocument()
  })
})
