import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CrashScreen } from './CrashScreen'

afterEach(() => vi.restoreAllMocks())

function Broken(): never {
  throw new Error('a screen failed to draw')
}

describe('CrashScreen', () => {
  it('says what happened and offers a way back instead of a blank page', () => {
    // React logs the error it caught; that is expected here.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <CrashScreen>
        <Broken />
      </CrashScreen>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong on this page.')
    expect(screen.getByRole('alert')).toHaveTextContent('Nothing you had already saved is affected.')
    expect(screen.getByRole('button', { name: 'Reload the page' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to your library' })).toHaveAttribute('href', '/')
  })

  it('stays out of the way when nothing is wrong', () => {
    render(
      <CrashScreen>
        <p>Fine</p>
      </CrashScreen>,
    )
    expect(screen.getByText('Fine')).toBeInTheDocument()
  })
})
