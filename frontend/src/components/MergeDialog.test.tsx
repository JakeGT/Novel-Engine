import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MergePreview } from '../api/client'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject } from '../test/harness'
import { MergeDialog, MergeWhat } from './MergeDialog'

afterEach(() => vi.unstubAllGlobals())

const twin = entity({ id: 'ent_twin', name: 'Mara V.', aliases: ['Captain Venn'], attributes: {} })
const harbor = entity({ id: 'ent_harbor', type: 'Location', name: 'Lantern Harbor', aliases: [] })
const tobin = entity({ id: 'ent_tobin', name: 'Tobin Ashe', aliases: [], attributes: {} })

const preview: MergePreview = {
  keep_id: 'ent_mara',
  keep_name: 'Mara Venn',
  merged_id: 'ent_twin',
  merged_name: 'Mara V.',
  entry_type: 'Character',
  name: 'Mara Venn',
  aliases: ['The Fox', 'Mara V.', 'Captain Venn'],
  details: { role: 'pilot' },
  names_left_out: 0,
  facts_moved: ['Tobin Ashe knows Mara V.'],
  facts_ended: [{ sentence: 'Mara V. is located at Saltmere', reason: 'the kept entry already has a value for this, and holds only one' }],
  beliefs_moved: [],
  beliefs_ended: [],
  events: ['The meeting'],
  plan_items: ['On the quay'],
  pictures_moved: 1,
  pictures_ended: 0,
  still_naming: [],
  claims_moved: ['what is said in “Tobin’s warning”'],
  change_count: 6,
}

describe('MergeDialog', () => {
  it('offers likely twins first, shows what would move, and sends the merge to review', async () => {
    const api = mockApi({
      'GET /entities': { items: [entity(), twin, harbor, tobin] },
      'POST /merges/preview': preview,
      'POST /merges': { mutation_id: 'mut_9', status: 'needs_review', validation_ok: true, validation_issues: [] },
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    const { container } = renderInProject(<MergeDialog entity={entity()} onClose={onClose} />)

    const dialog = screen.getByRole('dialog', { name: 'Merge “Mara Venn” with another entry' })
    // Only entries of the same type, the one sharing a name first.
    const choices = await within(dialog).findAllByRole('radio')
    expect(choices.map((choice) => choice.closest('label')?.textContent)).toEqual([
      'Mara V. — also Captain Venn',
      'Tobin Ashe',
    ])
    expect(within(dialog).getByRole('button', { name: 'Send merge to review' })).toBeDisabled()
    expect(within(dialog).getByText('Choose the other entry to see what the merge would do.')).toBeInTheDocument()

    await user.click(choices[0])
    expect(await within(dialog).findByText('Tobin Ashe knows Mara V.')).toBeInTheDocument()
    expect(within(dialog).getByText(/holds only one/)).toBeInTheDocument()
    expect(within(dialog).getByText('The meeting')).toBeInTheDocument()
    // What a character said follows the kept entry (AST-EXP-017).
    expect(within(dialog).getByText('what is said in “Tobin’s warning”')).toBeInTheDocument()
    expect(api.find('POST', '/merges/preview')[0].body).toEqual({
      entity_id: 'ent_twin',
      into: 'ent_mara',
      name: null,
      suggested: false,
    })
    await expectAccessible(container)

    // Keep the other one instead, under its name.
    await user.click(within(dialog).getByRole('radio', { name: 'Keep “Mara V.” and archive “Mara Venn”' }))
    await waitFor(() =>
      expect(api.find('POST', '/merges/preview').at(-1)?.body).toMatchObject({
        entity_id: 'ent_mara',
        into: 'ent_twin',
      }),
    )
    await user.click(within(dialog).getByRole('radio', { name: 'Keep “Mara Venn” and archive “Mara V.”' }))
    await user.click(within(dialog).getByRole('checkbox', { name: 'Call the kept entry “Mara V.” instead of “Mara Venn”' }))
    await waitFor(() => expect(api.find('POST', '/merges/preview').at(-1)?.body).toMatchObject({ name: 'Mara V.' }))

    await user.click(await within(dialog).findByRole('button', { name: 'Send merge to review' }))
    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_9'),
    )
    expect(api.find('POST', '/merges')[0].body).toEqual({
      entity_id: 'ent_twin',
      into: 'ent_mara',
      name: 'Mara V.',
      expected_revision: 5,
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the dialog and what was chosen when sending fails', async () => {
    mockApi({
      'GET /entities': { items: [entity(), twin] },
      'POST /merges/preview': preview,
      'POST /merges': problem(409, 'head_conflict', 'The story changed while you were choosing.'),
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderInProject(<MergeDialog entity={entity()} onClose={onClose} />)
    await user.click(await screen.findByRole('radio', { name: /also Captain Venn/ }))
    await user.click(await screen.findByRole('button', { name: 'Send merge to review' }))
    expect(await screen.findByText(/Could not send the merge to review/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /also Captain Venn/ })).toBeChecked()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('says plainly when there is no other entry of the same type', async () => {
    mockApi({ 'GET /entities': { items: [entity(), harbor] } })
    renderInProject(<MergeDialog entity={entity()} onClose={vi.fn()} />)
    expect(
      await screen.findByText('There is no other character to merge with. Only entries of the same type can be merged.'),
    ).toBeInTheDocument()
  })
})

describe('MergeWhat', () => {
  it('shows in review what a suggested merge would move now', async () => {
    const api = mockApi({ 'POST /merges/preview': preview })
    renderInProject(
      <MergeWhat payload={{ op: 'merge_entities', id: 'ent_twin', into: 'ent_mara', drop_facts: ['fct_1'] }} open />,
    )
    expect(await screen.findByText('Tobin Ashe knows Mara V.')).toBeInTheDocument()
    expect(api.find('POST', '/merges/preview')[0].body).toEqual({
      entity_id: 'ent_twin',
      into: 'ent_mara',
      name: null,
      drop_facts: ['fct_1'],
      drop_beliefs: [],
      suggested: true,
    })
  })

  it('says in review when the kept entry cannot keep every other name', async () => {
    mockApi({ 'POST /merges/preview': { ...preview, names_left_out: 3 } })
    renderInProject(<MergeWhat payload={{ op: 'merge_entities', id: 'ent_twin', into: 'ent_mara' }} open />)
    expect(await screen.findByText(/3 other names left out: an entry keeps at most 3/)).toBeInTheDocument()
  })

  it('says so when the merge cannot be worked out any more', async () => {
    mockApi({})
    renderInProject(<MergeWhat payload={{ id: 'ent_twin', into: 'ent_mara' }} open />)
    expect(await screen.findByText(/cannot be worked out now/)).toBeInTheDocument()
  })
})
