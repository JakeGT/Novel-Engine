import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { PassEstimate } from '../api/client'
import { PassChoices } from './CheckPasses'

const estimate = (check: PassEstimate['check_pass'], total: number): PassEstimate =>
  ({ check_pass: check, label: check, description: '', prompt_tokens: total, completion_tokens: 0, total_tokens: total }) as PassEstimate

const text = (['viewpoint', 'continuity', 'plan', 'voice', 'clarity'] as const).map((item) => estimate(item, 1000))

describe('PassChoices and pictures', () => {
  it('offers the pictures check only for a scene with pictures it can use', async () => {
    const { rerender } = render(<PassChoices passes={[...text, estimate('pictures', 0)]} chosen={[]} onChange={vi.fn()} />)
    expect(screen.queryByRole('checkbox', { name: /pictures/ })).not.toBeInTheDocument()

    const onChange = vi.fn()
    rerender(<PassChoices passes={[...text, estimate('pictures', 2400)]} chosen={['clarity']} onChange={onChange} />)
    const pictures = screen.getByRole('checkbox', { name: /pictures.*about 2,400 tokens/ })
    expect(pictures).not.toBeChecked()
    await userEvent.setup().click(pictures)
    expect(onChange).toHaveBeenCalledWith(['clarity', 'pictures'])
  })
})
