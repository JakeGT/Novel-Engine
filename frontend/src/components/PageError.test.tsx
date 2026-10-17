import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { expectAccessible } from '../test/harness'
import { PageError } from './PageError'

function Broken(): never {
  throw new Error('a render failure')
}

describe('PageError', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows what happened and two ways on instead of a blank page', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(
      <MemoryRouter>
        <PageError home="/projects/prj_1">
          <Broken />
        </PageError>
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'This page stopped working' })).toBeInTheDocument()
    expect(screen.getByText(/Nothing you saved is affected/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload the page' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go back to the start' })).toHaveAttribute('href', '/projects/prj_1')
    await expectAccessible(container)
  })

  it('shows the page as usual when nothing goes wrong', () => {
    render(
      <MemoryRouter>
        <PageError home="/">
          <p>All well</p>
        </PageError>
      </MemoryRouter>,
    )
    expect(screen.getByText('All well')).toBeInTheDocument()
  })
})
