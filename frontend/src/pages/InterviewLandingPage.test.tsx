import { render, screen, waitForElementToBeRemoved } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as publicApi from '@/api/public'
import type { InterviewLanding } from '@/api/types'
import { InterviewLandingPage } from './InterviewLandingPage'

function renderPage(token: string) {
  return render(
    <MemoryRouter initialEntries={[`/interview/${token}`]}>
      <Routes>
        <Route path="/interview/:token" element={<InterviewLandingPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const BASE: InterviewLanding = {
  position_title: 'Junior AI Engineer',
  company_name: 'FlairsTech',
  candidate_first_name: 'Jordan',
  expires_at: '2026-09-08T00:00:00Z',
  stage: 'not_started',
  voice_invite_url: null,
}

afterEach(() => {
  vi.useRealTimers()
})

describe('InterviewLandingPage', () => {
  it('shows a Start button for a not-yet-started invitation', async () => {
    vi.spyOn(publicApi, 'getInterviewLanding').mockResolvedValue(BASE)

    renderPage('a-valid-token')

    expect(await screen.findByText(/hi jordan/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start interview/i })).toBeInTheDocument()
  })

  it('arms the queue item on Start and shows the preparing state', async () => {
    vi.spyOn(publicApi, 'getInterviewLanding').mockResolvedValue(BASE)
    const start = vi.spyOn(publicApi, 'startInterview').mockResolvedValue({
      ...BASE, stage: 'preparing',
    })
    vi.spyOn(publicApi, 'getInterviewStatus').mockResolvedValue({ ...BASE, stage: 'preparing' })

    renderPage('a-valid-token')
    await screen.findByRole('button', { name: /start interview/i })
    await userEvent.click(screen.getByRole('button', { name: /start interview/i }))

    expect(start).toHaveBeenCalledWith('a-valid-token')
    expect(await screen.findByText(/preparing your interview/i)).toBeInTheDocument()
  })

  it('shows the completed state for an already-finished interview', async () => {
    vi.spyOn(publicApi, 'getInterviewLanding').mockResolvedValue({ ...BASE, stage: 'completed' })

    renderPage('a-valid-token')

    expect(await screen.findByText(/already completed this interview/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start interview/i })).not.toBeInTheDocument()
  })

  it('shows a generic unavailable message for an expired, revoked, or unknown token', async () => {
    vi.spyOn(publicApi, 'getInterviewLanding').mockRejectedValue(new Error('Resource not found'))

    renderPage('bad-token')

    expect(await screen.findByText(/this interview link isn.t available/i)).toBeInTheDocument()
  })

  it(
    'surfaces an error after repeated failed polls instead of staying silently stuck on preparing',
    async () => {
      // Regression test for the Phase 2 handoff bug: a poll that keeps failing
      // (network hiccup, a backend error, anything) used to retry forever with
      // no visible feedback -- indistinguishable from an interview that is
      // genuinely stuck. It must self-heal past one or two misses without
      // alarming anyone, but say something once that stops looking transient.
      vi.spyOn(publicApi, 'getInterviewLanding').mockResolvedValue(BASE)
      vi.spyOn(publicApi, 'startInterview').mockResolvedValue({ ...BASE, stage: 'preparing' })
      // An empty-message rejection (e.g. a network failure with no server
      // detail to relay) is what exercises our own fallback copy below --
      // toMessage() prefers a real error's own message when it has one.
      vi.spyOn(publicApi, 'getInterviewStatus').mockRejectedValue(new Error())

      renderPage('a-valid-token')
      await screen.findByRole('button', { name: /start interview/i })
      await userEvent.click(screen.getByRole('button', { name: /start interview/i }))
      await screen.findByText(/preparing your interview/i)

      expect(screen.queryByText(/lost contact/i)).not.toBeInTheDocument()
      expect(
        await screen.findByText(/lost contact/i, {}, { timeout: 15000 }),
      ).toBeInTheDocument()
      // Still on the preparing screen, still trying -- a poll failure is never
      // treated as terminal the way "failed" (a settled queue item) is.
      expect(screen.getByRole('heading', { name: /preparing your interview/i })).toBeInTheDocument()
    },
    20000,
  )

  it('clears a poll error once polling succeeds again', async () => {
    vi.spyOn(publicApi, 'getInterviewLanding').mockResolvedValue(BASE)
    vi.spyOn(publicApi, 'startInterview').mockResolvedValue({ ...BASE, stage: 'preparing' })
    const getStatus = vi.spyOn(publicApi, 'getInterviewStatus')
    getStatus.mockRejectedValue(new Error())

    renderPage('a-valid-token')
    await screen.findByRole('button', { name: /start interview/i })
    await userEvent.click(screen.getByRole('button', { name: /start interview/i }))
    await screen.findByText(/lost contact/i, {}, { timeout: 15000 })

    getStatus.mockResolvedValue({ ...BASE, stage: 'preparing' })
    await waitForElementToBeRemoved(() => screen.queryByText(/lost contact/i), { timeout: 15000 })
  }, 20000)
})
