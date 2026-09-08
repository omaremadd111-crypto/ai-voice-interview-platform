import { request } from './client'
import { listCandidates } from './candidates'
import { listPositions } from './positions'
import type {
  CallQueue,
  Candidate,
  Position,
  QueueItem,
  QueueProgress,
  ScreeningResult,
} from './types'

export function listQueues(positionId?: number): Promise<CallQueue[]> {
  return request<CallQueue[]>('/api/v1/queues', { query: { position_id: positionId } })
}

export function getQueue(id: number): Promise<CallQueue> {
  return request<CallQueue>(`/api/v1/queues/${id}`)
}

export function createQueue(positionId: number, name: string): Promise<CallQueue> {
  return request<CallQueue>('/api/v1/queues', {
    method: 'POST',
    json: { position_id: positionId, name },
  })
}

export function renameQueue(id: number, name: string): Promise<CallQueue> {
  return request<CallQueue>(`/api/v1/queues/${id}`, { method: 'PATCH', json: { name } })
}

export function deleteQueue(id: number): Promise<void> {
  return request<void>(`/api/v1/queues/${id}`, { method: 'DELETE' })
}

/**
 * Start / Pause / Resume only change whether the background worker may claim this
 * queue's items. No screening runs in the browser, so navigating away — or closing
 * the tab entirely — never interrupts a candidate's interview.
 */
export function startQueue(id: number): Promise<CallQueue> {
  return request<CallQueue>(`/api/v1/queues/${id}/start`, { method: 'POST' })
}

export function pauseQueue(id: number): Promise<CallQueue> {
  return request<CallQueue>(`/api/v1/queues/${id}/pause`, { method: 'POST' })
}

export function resumeQueue(id: number): Promise<CallQueue> {
  return request<CallQueue>(`/api/v1/queues/${id}/resume`, { method: 'POST' })
}

export function getQueueProgress(id: number): Promise<QueueProgress> {
  return request<QueueProgress>(`/api/v1/queues/${id}/progress`)
}

export function listQueueItems(queueId: number): Promise<QueueItem[]> {
  return request<QueueItem[]>(`/api/v1/queues/${queueId}/items`)
}

export function getQueueItemResult(
  queueId: number,
  itemId: number,
): Promise<ScreeningResult | null> {
  return request<ScreeningResult | null>(`/api/v1/queues/${queueId}/items/${itemId}/result`)
}

export function addQueueItem(queueId: number, candidateId: number): Promise<QueueItem> {
  return request<QueueItem>(`/api/v1/queues/${queueId}/items`, {
    method: 'POST',
    json: { candidate_id: candidateId },
  })
}

export function removeQueueItem(queueId: number, itemId: number): Promise<void> {
  return request<void>(`/api/v1/queues/${queueId}/items/${itemId}`, { method: 'DELETE' })
}

export function cancelQueueItem(queueId: number, itemId: number): Promise<QueueItem> {
  return request<QueueItem>(`/api/v1/queues/${queueId}/items/${itemId}/cancel`, { method: 'POST' })
}

export function retryQueueItem(queueId: number, itemId: number): Promise<QueueItem> {
  return request<QueueItem>(`/api/v1/queues/${queueId}/items/${itemId}/retry`, { method: 'POST' })
}

export interface QueueWithPosition {
  queue: CallQueue
  position: Position
}

/**
 * The "all queues" view. `GET /api/v1/queues` already returns only the caller's
 * own queues, so this fans out over positions purely to attach the role each
 * queue screens for — assembled here rather than in the page, like
 * listAllCandidates().
 *
 * Excludes 'auto' queues: those are created and driven entirely by the
 * automated screening pipeline (see PositionPublishingService.publish()), not
 * by a recruiter clicking Start/Pause/Resume, so they do not belong in this
 * manual-batch overview. A published position's queue is still reachable by
 * its own URL; it simply is not listed here.
 */
export async function listAllQueues(): Promise<QueueWithPosition[]> {
  const [queues, positions] = await Promise.all([listQueues(), listPositions()])
  const byId = new Map(positions.map((position) => [position.id, position]))
  return queues
    .filter((queue) => queue.kind !== 'auto')
    .map((queue) => {
      const position = byId.get(queue.position_id)
      return position === undefined ? null : { queue, position }
    })
    .filter((row): row is QueueWithPosition => row !== null)
}

export interface QueueDetail {
  queue: CallQueue
  position: Position
  items: QueueItem[]
  progress: QueueProgress
  /** Everyone on the queue's position, so items can be shown by name and the
   *  "add candidate" picker can offer the ones not queued yet. */
  candidates: Candidate[]
  results: Record<number, ScreeningResult | null>
}

export async function loadQueueDetail(queueId: number): Promise<QueueDetail> {
  const queue = await getQueue(queueId)
  const [position, items, progress, candidates] = await Promise.all([
    request<Position>(`/api/v1/positions/${queue.position_id}`),
    listQueueItems(queueId),
    getQueueProgress(queueId),
    listCandidates(queue.position_id),
  ])
  const completedResults = await Promise.all(
    items
      .filter((item) => item.status === 'completed')
      .map(async (item) => [item.id, await getQueueItemResult(queueId, item.id)] as const),
  )
  return {
    queue,
    position,
    items,
    progress,
    candidates,
    results: Object.fromEntries(completedResults),
  }
}
