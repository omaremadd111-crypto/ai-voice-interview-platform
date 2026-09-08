import { request } from './client'
import type { ScreeningConfig, ScreeningConfigUpdate } from './types'

export function getScreeningConfig(positionId: number): Promise<ScreeningConfig> {
  return request<ScreeningConfig>(`/api/v1/positions/${positionId}/screening-config`)
}

export function updateScreeningConfig(
  positionId: number,
  payload: ScreeningConfigUpdate,
): Promise<ScreeningConfig> {
  return request<ScreeningConfig>(`/api/v1/positions/${positionId}/screening-config`, {
    method: 'PUT',
    json: payload,
  })
}

export function approveScreeningTemplate(positionId: number): Promise<ScreeningConfig> {
  return request<ScreeningConfig>(`/api/v1/positions/${positionId}/screening-template/approve`, {
    method: 'POST',
  })
}

export function publishPosition(positionId: number): Promise<ScreeningConfig> {
  return request<ScreeningConfig>(`/api/v1/positions/${positionId}/publish`, { method: 'POST' })
}

export function unpublishPosition(positionId: number): Promise<ScreeningConfig> {
  return request<ScreeningConfig>(`/api/v1/positions/${positionId}/unpublish`, { method: 'POST' })
}
