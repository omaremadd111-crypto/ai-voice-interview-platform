import { request } from './client'
import { listPositions } from './positions'
import type { Candidate, CandidateCreate, CandidateUpdate, Position, ScreeningResult } from './types'

export function listCandidates(positionId: number): Promise<Candidate[]> {
  return request<Candidate[]>('/api/v1/candidates', { query: { position_id: positionId } })
}

export function getCandidate(id: number): Promise<Candidate> {
  return request<Candidate>(`/api/v1/candidates/${id}`)
}

export function getCandidateResult(id: number): Promise<ScreeningResult | null> {
  return request<ScreeningResult | null>(`/api/v1/candidates/${id}/result`)
}

export function createCandidate(payload: CandidateCreate): Promise<Candidate> {
  return request<Candidate>('/api/v1/candidates', { method: 'POST', json: payload })
}

export function updateCandidate(id: number, payload: CandidateUpdate): Promise<Candidate> {
  return request<Candidate>(`/api/v1/candidates/${id}`, { method: 'PATCH', json: payload })
}

/** File extensions the backend's DocumentParser accepts. Mirrors
 *  DocumentParser.SUPPORTED_EXTENSIONS -- used for the file picker's accept
 *  attribute and for a friendly client-side check before uploading. */
export const SUPPORTED_CV_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md'] as const

/** Mirrors Settings.max_cv_upload_bytes (5 MB). */
export const MAX_CV_UPLOAD_BYTES = 5 * 1024 * 1024

export function uploadCandidateCv(candidateId: number, file: File): Promise<Candidate> {
  const formData = new FormData()
  formData.append('file', file)
  return request<Candidate>(`/api/v1/candidates/${candidateId}/cv`, {
    method: 'POST',
    formData,
  })
}

export interface CandidateWithPosition {
  candidate: Candidate
  position: Position
}

/**
 * The backend intentionally has no cross-position candidate endpoint --
 * `GET /api/v1/candidates` always requires a `position_id`, because candidates
 * are owned through their position. The "All candidates" view therefore fans out
 * over the user's own positions and joins the results here, in the API layer,
 * so pages never assemble multi-request views themselves.
 */
export async function listAllCandidates(): Promise<CandidateWithPosition[]> {
  const positions = await listPositions()
  const perPosition = await Promise.all(
    positions.map(async (position) => {
      const candidates = await listCandidates(position.id)
      return candidates.map((candidate) => ({ candidate, position }))
    }),
  )
  return perPosition.flat()
}
