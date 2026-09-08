import { request } from './client'
import type { InvitationIssued, PipelineRow } from './types'

export function getPipeline(positionId: number): Promise<PipelineRow[]> {
  return request<PipelineRow[]>(`/api/v1/positions/${positionId}/pipeline`)
}

/** Creates a fresh interview invitation for an application's candidate, or
 *  rotates the existing one -- same action either way, see
 *  application/interview_invitation_service.py's issue_or_rotate. */
export function issuePipelineInvitation(
  positionId: number,
  applicationId: number,
): Promise<InvitationIssued> {
  return request<InvitationIssued>(
    `/api/v1/positions/${positionId}/pipeline/${applicationId}/invitation`,
    { method: 'POST' },
  )
}
