import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as pipelineApi from '@/api/pipeline'
import type { PipelineRow } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { PipelineBoard } from './PipelineBoard'

const ROW: PipelineRow = {
  application_id: 7,
  candidate_id: 21,
  full_name: 'Jordan Rivera',
  email: 'jordan@example.com',
  phone: null,
  pipeline_state: 'plan_ready',
  cv_parse_error: null,
  last_error: null,
  created_at: '2026-09-01T00:00:00Z',
}

describe('PipelineBoard', () => {
  it('shows an empty state when nobody has applied yet', async () => {
    vi.spyOn(pipelineApi, 'getPipeline').mockResolvedValue([])

    renderWithProviders(<PipelineBoard positionId={5} />)

    expect(await screen.findByText(/no applications yet/i)).toBeInTheDocument()
  })

  it('lists applicants with their stage, and can issue an interview link', async () => {
    vi.spyOn(pipelineApi, 'getPipeline').mockResolvedValue([ROW])
    const issue = vi.spyOn(pipelineApi, 'issuePipelineInvitation').mockResolvedValue({
      expires_at: '2026-09-08T00:00:00Z',
      interview_url: 'http://localhost:5173/interview/fresh-token',
    })

    renderWithProviders(<PipelineBoard positionId={5} />)

    expect(await screen.findByText('Jordan Rivera')).toBeInTheDocument()
    expect(screen.getByText('jordan@example.com')).toBeInTheDocument()
    expect(screen.getByText('Queued')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /issue link/i }))
    expect(issue).toHaveBeenCalledWith(5, 7)
    expect(await screen.findByText('http://localhost:5173/interview/fresh-token')).toBeInTheDocument()
  })

  it('disables the action for an application with no candidate yet', async () => {
    vi.spyOn(pipelineApi, 'getPipeline').mockResolvedValue([{ ...ROW, candidate_id: null }])

    renderWithProviders(<PipelineBoard positionId={5} />)

    expect(await screen.findByText('Jordan Rivera')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /issue link/i })).toBeDisabled()
  })

  it('surfaces a CV parse failure inline', async () => {
    vi.spyOn(pipelineApi, 'getPipeline').mockResolvedValue([{ ...ROW, cv_parse_error: 'corrupted file' }])

    renderWithProviders(<PipelineBoard positionId={5} />)

    expect(await screen.findByText(/cv could not be read/i)).toBeInTheDocument()
  })
})
