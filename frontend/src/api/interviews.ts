import { request } from './client'
import type { PrepareInterviewResult } from './types'

/**
 * Calls the backend's position-driven preparation endpoint, which reuses the
 * existing InterviewAgentService. The frontend never builds an interview plan
 * itself -- it only renders what the backend returns for HR review.
 */
export function prepareInterview(
  positionId: number,
  candidateId: number,
): Promise<PrepareInterviewResult> {
  return request<PrepareInterviewResult>('/api/v1/interviews/prepare', {
    method: 'POST',
    json: { position_id: positionId, candidate_id: candidateId },
  })
}
