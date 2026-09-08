import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as positionsApi from '@/api/positions'
import * as queuesApi from '@/api/queues'
import type { CallQueue, Position } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { QueuesPage } from './QueuesPage'

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

const QUEUE: CallQueue = {
  id: 3,
  position_id: 7,
  name: 'Junior AI Engineer — round 1',
  status: 'idle',
  kind: 'manual',
  created_at: null,
  updated_at: null,
}

function stub({ queues = [] }: { queues?: CallQueue[] } = {}) {
  vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([POSITION])
  vi.spyOn(queuesApi, 'listAllQueues').mockResolvedValue(
    queues.map((queue) => ({ queue, position: POSITION })),
  )
}

describe('QueuesPage', () => {
  it('lists the queues with the position each screens for', async () => {
    stub({ queues: [QUEUE] })
    renderWithProviders(<QueuesPage />)

    expect(await screen.findByRole('link', { name: QUEUE.name })).toHaveAttribute(
      'href',
      '/queues/3',
    )
    expect(screen.getByText('Junior AI Engineer')).toBeInTheDocument()
    expect(screen.getByText(/not started/i)).toBeInTheDocument()
  })

  it('offers to create the first queue when there are none', async () => {
    stub()
    renderWithProviders(<QueuesPage />)

    expect(await screen.findByText(/no queues yet/i)).toBeInTheDocument()
    expect(screen.getByText(/approved interview plan/i)).toBeInTheDocument()
  })

  it('creates a queue for the chosen position', async () => {
    stub()
    const create = vi.spyOn(queuesApi, 'createQueue').mockResolvedValue(QUEUE)

    renderWithProviders(<QueuesPage />)
    await userEvent.click((await screen.findAllByRole('button', { name: /new queue/i }))[0]!)

    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/queue name/i), 'Round 1')
    await userEvent.click(within(dialog).getByRole('button', { name: /create queue/i }))

    await waitFor(() => expect(create).toHaveBeenCalledWith(7, 'Round 1'))
    expect(await screen.findByRole('status')).toHaveTextContent(/was created/i)
  })

  it('keeps a creation failure in the dialog', async () => {
    const { ApiError } = await import('@/api/client')
    stub()
    vi.spyOn(queuesApi, 'createQueue').mockRejectedValue(
      new ApiError(403, 'You do not have access to this resource'),
    )

    renderWithProviders(<QueuesPage />)
    await userEvent.click((await screen.findAllByRole('button', { name: /new queue/i }))[0]!)
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/queue name/i), 'Round 1')
    await userEvent.click(within(dialog).getByRole('button', { name: /create queue/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/do not have access/i)
  })

  it('sends people to positions first when they have none', async () => {
    vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([])
    vi.spyOn(queuesApi, 'listAllQueues').mockResolvedValue([])

    renderWithProviders(<QueuesPage />)

    expect(await screen.findByText(/create a position first/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /new queue/i })).toBeDisabled()
  })

  it('surfaces a load failure instead of showing an empty queue list', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([POSITION])
    vi.spyOn(queuesApi, 'listAllQueues').mockRejectedValue(
      new ApiError(0, 'Could not reach the server. Check that the API is running.'),
    )

    renderWithProviders(<QueuesPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach the server/i)
    expect(screen.queryByText(/no queues yet/i)).not.toBeInTheDocument()
  })
})
