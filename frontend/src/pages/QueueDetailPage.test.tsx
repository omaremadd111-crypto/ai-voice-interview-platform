import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as queuesApi from '@/api/queues'
import type { QueueDetail } from '@/api/queues'
import type {
  CallQueue,
  Candidate,
  Position,
  QueueItem,
  QueueItemStatus,
  QueueStatus,
  ScreeningResult,
} from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { QueueDetailPage } from './QueueDetailPage'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useParams: () => ({ queueId: '3' }) }
})

const POSITION: Position = {
  id: 7,
  owner_id: 1,
  company_name: 'Northwind Labs',
  title: 'Junior AI Engineer',
  description: null,
  experience_level: 'Junior',
  pass_score_threshold: null,
  rubric_profile: null,
  status: 'active',
}

const CANDIDATES: Candidate[] = [
  {
    id: 11,
    position_id: 7,
    full_name: 'Jordan Rivera',
    email: null,
    phone: null,
    cv_text: null,
    cv_filename: null,
    status: 'queued',
  },
  {
    id: 12,
    position_id: 7,
    full_name: 'Sam Okafor',
    email: null,
    phone: null,
    cv_text: null,
    cv_filename: null,
    status: 'new',
  },
]

function item(overrides: Partial<QueueItem> = {}): QueueItem {
  return {
    id: 100,
    queue_id: 3,
    candidate_id: 11,
    status: 'pending',
    attempts: 0,
    max_attempts: 3,
    claimed_by: null,
    claimed_at: null,
    lease_expires_at: null,
    next_attempt_at: null,
    last_error: null,
    interview_session_id: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

function counts(overrides: Partial<Record<QueueItemStatus, number>> = {}) {
  return {
    awaiting_candidate: 0,
    pending: 0,
    claimed: 0,
    in_progress: 0,
    completed: 0,
    failed: 0,
    no_answer: 0,
    cancelled: 0,
    ...overrides,
  }
}

function detail({
  status = 'idle' as QueueStatus,
  items = [item()],
  progressCounts = counts({ pending: 1 }),
  results = {},
}: {
  status?: QueueStatus
  items?: QueueItem[]
  progressCounts?: Record<QueueItemStatus, number>
  results?: Record<number, ScreeningResult | null>
} = {}): QueueDetail {
  const queue: CallQueue = {
    id: 3,
    position_id: 7,
    name: 'Junior AI Engineer — round 1',
    status,
    kind: 'manual',
    created_at: null,
    updated_at: null,
  }
  const total = Object.values(progressCounts).reduce((sum, value) => sum + value, 0)
  return {
    queue,
    position: POSITION,
    items,
    candidates: CANDIDATES,
    results,
    progress: {
      queue_id: 3,
      status,
      total,
      counts: progressCounts,
      finished: progressCounts.completed + progressCounts.failed + progressCounts.no_answer +
        progressCounts.cancelled,
      in_flight: progressCounts.claimed + progressCounts.in_progress,
    },
  }
}

const RESULT: ScreeningResult = {
  session_id: 'session-result',
  company: 'Northwind Labs',
  role: 'Junior AI Engineer',
  candidate_name: 'Jordan Rivera',
  overall_score: 78,
  evidence_coverage: 0.85,
  screening_outcome: 'PASS',
  recommendation: 'Proceed to deeper technical assessment',
  category_scores: [],
  strengths: ['Explained a retrieval pipeline with measurable outcomes.'],
  areas_to_validate: [],
  ai_summary: 'The candidate supplied evidence across the approved interview plan.',
  human_follow_up_questions: [],
  full_transcript: [],
  llm_provider: 'mock',
  is_mock: true,
  generated_at: '2026-08-23T10:00:00Z',
}

function stub(value: QueueDetail) {
  return vi.spyOn(queuesApi, 'loadQueueDetail').mockResolvedValue(value)
}

describe('QueueDetailPage', () => {
  it('shows who is in the queue and how far the worker has got', async () => {
    stub(
      detail({
        items: [item({ status: 'completed', attempts: 1 }), item({ id: 101, candidate_id: 12 })],
        progressCounts: counts({ completed: 1, pending: 1 }),
      }),
    )

    renderWithProviders(<QueueDetailPage />)

    expect(await screen.findByText(/1 of 2 finished/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Jordan Rivera' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sam Okafor' })).toBeInTheDocument()
    // Scoped to the table: the summary tiles use the same status wording.
    expect(within(screen.getByRole('table')).getByText(/screening completed/i)).toBeInTheDocument()
  })

  it('shows a completed screening outcome and links to the full candidate report', async () => {
    stub(detail({
      items: [item({ status: 'completed', attempts: 1, interview_session_id: 'session-result' })],
      progressCounts: counts({ completed: 1 }),
      results: { 100: RESULT },
    }))

    renderWithProviders(<QueueDetailPage />)

    const table = within(await screen.findByRole('table'))
    expect(table.getByText('PASS')).toBeInTheDocument()
    expect(table.getByText(/78\/100 · 85% evidence/i)).toBeInTheDocument()
    expect(table.getByRole('link', { name: /view full report/i })).toHaveAttribute(
      'href',
      '/candidates/11#screening-report',
    )
  })

  it('starts an idle queue', async () => {
    stub(detail())
    const start = vi.spyOn(queuesApi, 'startQueue').mockResolvedValue({
      ...detail({ status: 'running' }).queue,
    })

    renderWithProviders(<QueueDetailPage />)
    await userEvent.click(await screen.findByRole('button', { name: /^start$/i }))

    await waitFor(() => expect(start).toHaveBeenCalledWith(3))
  })

  it('pauses a running queue and resumes a paused one', async () => {
    stub(detail({ status: 'running' }))
    const pause = vi.spyOn(queuesApi, 'pauseQueue').mockResolvedValue(
      detail({ status: 'paused' }).queue,
    )

    const { unmount } = renderWithProviders(<QueueDetailPage />)
    await userEvent.click(await screen.findByRole('button', { name: /^pause$/i }))
    await waitFor(() => expect(pause).toHaveBeenCalledWith(3))
    unmount()

    stub(detail({ status: 'paused' }))
    const resume = vi.spyOn(queuesApi, 'resumeQueue').mockResolvedValue(
      detail({ status: 'running' }).queue,
    )
    renderWithProviders(<QueueDetailPage />)
    await userEvent.click(await screen.findByRole('button', { name: /^resume$/i }))
    await waitFor(() => expect(resume).toHaveBeenCalledWith(3))
  })

  it('cannot be started while it holds no candidates', async () => {
    stub(detail({ items: [], progressCounts: counts() }))
    renderWithProviders(<QueueDetailPage />)

    expect(await screen.findByRole('button', { name: /^start$/i })).toBeDisabled()
    expect(screen.getByText(/no candidates in this queue/i)).toBeInTheDocument()
  })

  it('adds a candidate who is not queued yet', async () => {
    stub(detail())
    const add = vi.spyOn(queuesApi, 'addQueueItem').mockResolvedValue(item({ id: 101, candidate_id: 12 }))

    renderWithProviders(<QueueDetailPage />)
    await userEvent.click((await screen.findAllByRole('button', { name: /add candidate/i }))[0]!)

    const dialog = await screen.findByRole('dialog')
    // Jordan is already in the queue, so only Sam is offered.
    const options = within(dialog).getAllByRole('option')
    expect(options).toHaveLength(1)
    expect(options[0]).toHaveTextContent('Sam Okafor')

    await userEvent.click(within(dialog).getByRole('button', { name: /add to queue/i }))
    await waitFor(() => expect(add).toHaveBeenCalledWith(3, 12))
  })

  it('explains when a candidate cannot be queued without an approved plan', async () => {
    const { ApiError } = await import('@/api/client')
    stub(detail())
    vi.spyOn(queuesApi, 'addQueueItem').mockRejectedValue(
      new ApiError(
        400,
        'Sam Okafor has no approved interview plan. Review and approve their plan before adding them to a calling queue.',
      ),
    )

    renderWithProviders(<QueueDetailPage />)
    await userEvent.click((await screen.findAllByRole('button', { name: /add candidate/i }))[0]!)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: /add to queue/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      /approve their plan before adding them/i,
    )
  })

  it('refuses to remove a candidate whose screening is under way', async () => {
    stub(
      detail({
        status: 'running',
        items: [item({ status: 'in_progress', attempts: 1, claimed_by: 'worker-a' })],
        progressCounts: counts({ in_progress: 1 }),
      }),
    )
    const remove = vi.spyOn(queuesApi, 'removeQueueItem')

    renderWithProviders(<QueueDetailPage />)

    const removeButton = await screen.findByRole('button', { name: /remove jordan rivera/i })
    expect(removeButton).toBeDisabled()
    expect(remove).not.toHaveBeenCalled()
    // The recruiter can see which worker holds it.
    expect(screen.getByText(/worker-a/i)).toBeInTheDocument()
  })

  it('offers a retry once the attempts are used up, and reports why', async () => {
    stub(
      detail({
        items: [
          item({
            status: 'no_answer',
            attempts: 3,
            last_error: 'Simulated no_answer from the null transport. No attempts remaining (3/3).',
          }),
        ],
        progressCounts: counts({ no_answer: 1 }),
      }),
    )
    const retry = vi.spyOn(queuesApi, 'retryQueueItem').mockResolvedValue(item())

    renderWithProviders(<QueueDetailPage />)

    const table = within(await screen.findByRole('table'))
    expect(table.getByText(/no answer/i)).toBeInTheDocument()
    expect(table.getByText(/no attempts remaining/i)).toBeInTheDocument()
    expect(table.getByText('3 / 3')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(retry).toHaveBeenCalledWith(3, 100))
  })

  it('shows when a waiting item is next due rather than implying it is stuck', async () => {
    stub(
      detail({
        items: [item({ status: 'pending', attempts: 1, next_attempt_at: '2026-08-22T12:00:00Z' })],
      }),
    )
    renderWithProviders(<QueueDetailPage />)

    expect(await screen.findByText(/next attempt/i)).toBeInTheDocument()
  })

  it('shows a proportional progress bar summarizing how far the queue has got', async () => {
    stub(
      detail({
        items: [item({ status: 'completed', attempts: 1 }), item({ id: 101, candidate_id: 12 })],
        progressCounts: counts({ completed: 1, pending: 1 }),
      }),
    )

    renderWithProviders(<QueueDetailPage />)

    expect(
      await screen.findByRole('img', { name: "50% of this queue's items are finished" }),
    ).toBeInTheDocument()
    expect(screen.getByText('50% complete')).toBeInTheDocument()
  })

  it('surfaces an action failure without losing the page', async () => {
    const { ApiError } = await import('@/api/client')
    stub(detail())
    vi.spyOn(queuesApi, 'startQueue').mockRejectedValue(
      new ApiError(403, 'You do not have access to this resource'),
    )

    renderWithProviders(<QueueDetailPage />)
    await userEvent.click(await screen.findByRole('button', { name: /^start$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have access/i)
    expect(screen.getByRole('link', { name: 'Jordan Rivera' })).toBeInTheDocument()
  })
})
