import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SchemaValidationReport, SchemaVersion } from '../api/client'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { SchemaPage } from './SchemaPage'

afterEach(() => vi.unstubAllGlobals())

function version(overrides: Partial<SchemaVersion> = {}): SchemaVersion {
  return {
    id: 'scv_1',
    workspace_id: 'ws_1',
    project_id: 'prj_1',
    version: 1,
    status: 'published',
    content_hash: 'h1',
    compatibility: 'additive',
    created_at: '2026-10-09T10:00:00Z',
    published_at: '2026-10-09T10:00:00Z',
    definition: { label: 'core', entity_extensions: [], predicate_extensions: [] },
    ...overrides,
  }
}

const report: SchemaValidationReport = {
  valid: true,
  content_hash: 'h2',
  compatibility: 'migration_required',
  errors: [],
  migration_plan: ['remove field Ship.rig: migrate 2 entities'],
  entity_types: ['Ship'],
  predicates: [],
}

describe('SchemaPage', () => {
  it('validates a draft, shows compatibility and the migration plan, then saves it', async () => {
    const api = mockApi({
      'GET /schemas': [version()],
      'POST /schemas/validate': report,
      'POST /schemas/drafts': {
        version: version({ id: 'scv_2', version: 2, status: 'draft', published_at: null }),
        compatibility: 'migration_required',
        migration_plan: report.migration_plan,
      },
    })
    const user = userEvent.setup()
    const { container } = renderInProject(<SchemaPage />)

    expect(await screen.findByText('Published')).toBeInTheDocument()
    expect(screen.getByText('prj_1')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save as draft' })
    expect(save).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Validate' }))
    expect(await screen.findByText('The definition is valid.')).toBeInTheDocument()
    expect(screen.getByText('Migration required — existing canon needs a plan')).toBeInTheDocument()
    expect(screen.getByText('remove field Ship.rig: migrate 2 entities')).toBeInTheDocument()
    await expectAccessible(container)

    await user.click(save)
    expect(await screen.findByText(/Saved as draft v2/)).toHaveTextContent(
      'changes nothing until it is published',
    )
    expect(api.find('POST', '/schemas/drafts')[0].body).toEqual({
      definition: { label: 'core', entity_extensions: [], predicate_extensions: [] },
    })
  })

  it('reports an invalid definition whether the server answers 200 or rejects it', async () => {
    let mode: 'report' | 'reject' = 'report'
    mockApi({
      'GET /schemas': [version()],
      'POST /schemas/validate': () =>
        mode === 'report'
          ? { ...report, valid: false, compatibility: 'invalid', errors: ['duplicate entity type Ship'], migration_plan: [] }
          : problem(400, 'validation_error', 'Invalid schema definition: label length out of bounds'),
    })
    const user = userEvent.setup()
    renderInProject(<SchemaPage />)

    await user.click(await screen.findByRole('button', { name: 'Validate' }))
    expect(await screen.findByText('duplicate entity type Ship')).toBeInTheDocument()
    expect(screen.getByText('Invalid definition')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save as draft' })).toBeDisabled()

    mode = 'reject'
    await user.click(screen.getByRole('button', { name: 'Validate' }))
    expect(await screen.findByText(/label length out of bounds/)).toBeInTheDocument()
    expect(screen.getByText('The definition is not valid.')).toBeInTheDocument()
  })

  it('catches malformed JSON before calling the server', async () => {
    const api = mockApi({ 'GET /schemas': [version()] })
    const user = userEvent.setup()
    renderInProject(<SchemaPage />)

    const editor = await screen.findByLabelText('Definition (JSON)')
    await user.clear(editor)
    await user.type(editor, 'not json')
    await user.click(screen.getByRole('button', { name: 'Validate' }))

    expect(editor).toHaveAttribute('aria-invalid', 'true')
    expect(editor).toHaveAccessibleDescription(/Not valid JSON/)
    expect(api.find('POST', '/schemas/validate')).toHaveLength(0)
  })

  it('shows clearly when a draft cannot be published', async () => {
    mockApi({
      'GET /schemas': [version(), version({ id: 'scv_2', version: 2, status: 'draft', published_at: null })],
      'POST /schemas/scv_2/publish': problem(409, 'schema_incompatible', 'removing entity type Ship is forbidden'),
    })
    const user = userEvent.setup()
    renderInProject(<SchemaPage />)

    await user.click(await screen.findByRole('button', { name: 'Publish v2' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This version cannot be published.')
    expect(alert).toHaveTextContent('removing entity type Ship is forbidden')
    expect(alert).toHaveTextContent('Canon was not changed.')
    await waitFor(() => expect(screen.getByText('Draft')).toBeInTheDocument())
  })
})
