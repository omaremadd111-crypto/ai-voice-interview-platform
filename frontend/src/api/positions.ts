import { request } from './client'
import type { Position, PositionCreate, PositionStatus, PositionUpdate } from './types'

export function listPositions(status?: PositionStatus): Promise<Position[]> {
  return request<Position[]>('/api/v1/positions', { query: { status } })
}

export function getPosition(id: number): Promise<Position> {
  return request<Position>(`/api/v1/positions/${id}`)
}

export function createPosition(payload: PositionCreate): Promise<Position> {
  return request<Position>('/api/v1/positions', { method: 'POST', json: payload })
}

export function updatePosition(id: number, payload: PositionUpdate): Promise<Position> {
  return request<Position>(`/api/v1/positions/${id}`, { method: 'PATCH', json: payload })
}

export function deletePosition(id: number): Promise<void> {
  return request<void>(`/api/v1/positions/${id}`, { method: 'DELETE' })
}
