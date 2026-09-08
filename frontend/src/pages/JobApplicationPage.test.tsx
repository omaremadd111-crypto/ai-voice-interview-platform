import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import * as publicApi from '@/api/public'
import type { PublicJob } from '@/api/types'
import { JobApplicationPage } from './JobApplicationPage'

// No renderWithProviders/AuthProvider here on purpose: this page is public and
// never calls useAuth(), exactly like VoiceInterviewPage's own test -- and
// useParams() needs a real matched <Route>, not just a MemoryRouter entry.
function renderPage(slug: string) {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${slug}`]}>
      <Routes>
        <Route path="/jobs/:slug" element={<JobApplicationPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const JOB: PublicJob = {
  slug: 'junior-ai-engineer-ab12cd34',
  title: 'Junior AI Engineer',
  company_name: 'FlairsTech',
  description: 'Build and ship the screening product.',
  experience_level: 'Junior',
  require_phone: false,
  application_notice: 'This role is screened by an AI interviewer.',
}

async function fillRequiredFields() {
  await userEvent.type(screen.getByLabelText(/full name/i), 'Jordan Rivera')
  await userEvent.type(screen.getByLabelText(/email/i), 'jordan@example.com')
  await userEvent.click(screen.getByLabelText(/i have read the notice/i))
}

describe('JobApplicationPage', () => {
  it('shows the job posting and an apply form for a published position', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockResolvedValue(JOB)

    renderPage('junior-ai-engineer-ab12cd34')

    expect(await screen.findByRole('heading', { name: 'Junior AI Engineer' })).toBeInTheDocument()
    expect(screen.getByText('FlairsTech')).toBeInTheDocument()
    expect(screen.getByText('Build and ship the screening product.')).toBeInTheDocument()
    expect(screen.getByText('This role is screened by an AI interviewer.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit application/i })).toBeInTheDocument()
  })

  it('shows a generic unavailable message for an unpublished or unknown slug', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockRejectedValue(new Error('Resource not found'))

    renderPage('does-not-exist')

    expect(await screen.findByText(/this job posting isn.t available/i)).toBeInTheDocument()
  })

  it('refuses to submit without consent, and never calls the API', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockResolvedValue(JOB)
    const apply = vi.spyOn(publicApi, 'applyToJob')

    renderPage('junior-ai-engineer-ab12cd34')
    await screen.findByRole('heading', { name: 'Junior AI Engineer' })

    await userEvent.type(screen.getByLabelText(/full name/i), 'Jordan Rivera')
    await userEvent.type(screen.getByLabelText(/email/i), 'jordan@example.com')
    await userEvent.click(screen.getByRole('button', { name: /submit application/i }))

    expect(await screen.findByText(/accept the notice/i)).toBeInTheDocument()
    expect(apply).not.toHaveBeenCalled()
  })

  it('submits the application and shows the confirmation with a start-interview link', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockResolvedValue(JOB)
    const apply = vi.spyOn(publicApi, 'applyToJob').mockResolvedValue({
      application_id: 42,
      interview_token: 'a-fresh-token',
    })

    renderPage('junior-ai-engineer-ab12cd34')
    await screen.findByRole('heading', { name: 'Junior AI Engineer' })
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: /submit application/i }))

    expect(apply).toHaveBeenCalledWith(
      'junior-ai-engineer-ab12cd34',
      expect.objectContaining({ fullName: 'Jordan Rivera', email: 'jordan@example.com', consent: true }),
    )
    expect(await screen.findByText(/application submitted/i)).toBeInTheDocument()
    const startLink = screen.getByRole('link', { name: /start interview now/i })
    expect(startLink).toHaveAttribute('href', '/interview/a-fresh-token')
  })

  it('shows a hand-off confirmation when no invitation was issued automatically', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockResolvedValue(JOB)
    vi.spyOn(publicApi, 'applyToJob').mockResolvedValue({ application_id: 42, interview_token: null })

    renderPage('junior-ai-engineer-ab12cd34')
    await screen.findByRole('heading', { name: 'Junior AI Engineer' })
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: /submit application/i }))

    expect(await screen.findByText(/application submitted/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /start interview now/i })).not.toBeInTheDocument()
    expect(screen.getByText(/hiring team will review/i)).toBeInTheDocument()
  })

  it('requires a phone number when the position asks for one', async () => {
    vi.spyOn(publicApi, 'getPublicJob').mockResolvedValue({ ...JOB, require_phone: true })

    renderPage('junior-ai-engineer-ab12cd34')
    await screen.findByRole('heading', { name: 'Junior AI Engineer' })

    expect(screen.getByLabelText(/phone/i)).toBeRequired()
  })
})
