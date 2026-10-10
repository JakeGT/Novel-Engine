import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { entity } from '../test/fixtures'
import { expectAccessible, mockApi, problem, renderInProject, reply } from '../test/harness'
import { AssistFlow } from './AssistFlow'

afterEach(() => vi.unstubAllGlobals())

const TYPES = ['Character', 'Location', 'Faction', 'Secret']
const proposal = {
  session_id: 'brf_1',
  mutation_id: 'mut_5',
  status: 'needs_review',
  operation_count: 3,
  discarded: [],
  validation_ok: true,
  validation_issues: [],
}
const sheet = {
  questions: [
    { id: 'q1', question: 'Where does she live?', category: 'place' },
    { id: 'q2', question: 'Who does she trust?', category: 'relationships' },
  ],
}

describe('AssistFlow', () => {
  it('goes from a description, through questions, to a draft held for review', async () => {
    const api = mockApi({
      'POST /assist/questions': sheet,
      'POST /assist/proposals': reply(201, proposal),
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    const { container } = renderInProject(<AssistFlow types={TYPES} onClose={onClose} />)

    // A description is the only thing that is needed.
    await user.click(screen.getByRole('button', { name: 'Ask me a few questions' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Write a sentence or two')
    expect(api.find('POST', '/assist/questions')).toHaveLength(0)
    await expectAccessible(container)

    await user.click(screen.getByRole('radio', { name: 'Character' }))
    await user.type(screen.getByLabelText('Character name (optional)'), 'Mara Venn')
    await user.type(screen.getByLabelText('Describe it in your own words'), 'A harbor pilot.')
    await user.click(screen.getByRole('button', { name: 'Ask me a few questions' }))

    const questions = await screen.findByRole('form', { name: 'A few questions' })
    expect(api.find('POST', '/assist/questions')[0].body).toEqual({
      description: 'A harbor pilot.',
      entity_type: 'Character',
      name: 'Mara Venn',
    })
    await user.type(within(questions).getByLabelText('Where does she live?'), 'Saltmere')
    await user.click(within(questions).getByRole('button', { name: 'Draft it for me to review' }))

    await waitFor(() =>
      expect(screen.getAllByTestId('location').at(-1)).toHaveTextContent('/projects/prj_1/review/mut_5'),
    )
    // Only answered questions are sent, against the freshly read version.
    expect(api.find('POST', '/assist/proposals')[0].body).toEqual({
      description: 'A harbor pilot.',
      entity_type: 'Character',
      name: 'Mara Venn',
      answers: [{ question_id: 'q1', question: 'Where does she live?', answer: 'Saltmere' }],
      expected_revision: 5,
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('can draft a whole idea straight from the description, with no type chosen', async () => {
    const api = mockApi({ 'POST /assist/proposals': reply(201, proposal) })
    const user = userEvent.setup()
    renderInProject(<AssistFlow types={TYPES} onClose={vi.fn()} />)

    expect(screen.getByRole('radio', { name: 'Let the assistant decide' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Skip questions and draft it' })).toBeDisabled()
    await user.type(
      screen.getByLabelText('Describe it in your own words'),
      'Mara is a pilot in the Salt Guild.',
    )
    await user.click(screen.getByRole('button', { name: 'Skip questions and draft it' }))

    await waitFor(() => expect(api.find('POST', '/assist/proposals')).toHaveLength(1))
    expect(api.find('POST', '/assist/proposals')[0].body).toEqual({
      description: 'Mara is a pilot in the Salt Guild.',
      entity_type: null,
      name: null,
      answers: [],
      expected_revision: 5,
    })
  })

  it('fills in an existing entry without needing a description', async () => {
    const api = mockApi({
      'POST /assist/questions': sheet,
      'POST /assist/proposals': reply(201, proposal),
    })
    const user = userEvent.setup()
    renderInProject(<AssistFlow entity={entity()} types={TYPES} onClose={vi.fn()} />)

    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Ask me a few questions' }))
    const questions = await screen.findByRole('form', { name: 'A few questions' })
    expect(api.find('POST', '/assist/questions')[0].body).toEqual({ entity_id: 'ent_mara', description: '' })

    // With no description and no answers there is nothing to draft from.
    const draft = within(questions).getByRole('button', { name: 'Draft it for me to review' })
    expect(draft).toBeDisabled()
    await user.type(within(questions).getByLabelText('Who does she trust?'), 'Nobody.')
    await user.click(draft)
    await waitFor(() => expect(api.find('POST', '/assist/proposals')).toHaveLength(1))
    expect(api.find('POST', '/assist/proposals')[0].body).toMatchObject({
      entity_id: 'ent_mara',
      answers: [{ question_id: 'q2', answer: 'Nobody.' }],
    })
  })

  it('falls back to typing it in by hand when no AI provider is connected', async () => {
    mockApi({ 'POST /assist/questions': problem(503, 'capability_unavailable', 'No provider.') })
    const onManual = vi.fn()
    const user = userEvent.setup()
    renderInProject(<AssistFlow types={TYPES} initialType="Location" onClose={vi.fn()} onManual={onManual} />)

    await user.type(screen.getByLabelText('Location name (optional)'), 'Saltmere')
    await user.type(screen.getByLabelText('Describe it in your own words'), 'A fishing town.')
    await user.click(screen.getByRole('button', { name: 'Ask me a few questions' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The assistant isn’t available right now')
    expect(alert).toHaveTextContent('Nothing was changed')
    expect(within(alert).getByRole('link', { name: 'Connect an AI key' })).toHaveAttribute(
      'href',
      '/projects/prj_1/assistant',
    )
    // What was typed is kept and carried into the manual form.
    expect(screen.getByLabelText('Describe it in your own words')).toHaveValue('A fishing town.')
    await user.click(within(alert).getByRole('button', { name: 'Fill it in by hand' }))
    expect(onManual).toHaveBeenCalledWith({ type: 'Location', name: 'Saltmere' })
  })
})
