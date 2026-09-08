/**
 * Unauthenticated calls only — no Authorization header is ever attached by
 * `request()` when there is no stored token, which is exactly the case on the
 * public job, apply, and interview-landing pages (they render outside
 * AuthProvider's protected routes).
 */
import { request } from './client'
import type { ApplyResult, InterviewLanding, PublicJob } from './types'

export function getPublicJob(slug: string): Promise<PublicJob> {
  return request<PublicJob>(`/api/v1/public/jobs/${encodeURIComponent(slug)}`)
}

/**
 * Submits the public application form. Always multipart/form-data (even with
 * no CV attached) so the backend's Content-Length pre-check and form-field
 * parsing stay on one code path — see api/routers/public.py's apply_to_job.
 */
export function applyToJob(
  slug: string,
  fields: { fullName: string; email: string; phone?: string; consent: boolean; cvFile?: File | null },
): Promise<ApplyResult> {
  const formData = new FormData()
  formData.append('full_name', fields.fullName)
  formData.append('email', fields.email)
  if (fields.phone) formData.append('phone', fields.phone)
  formData.append('consent', String(fields.consent))
  if (fields.cvFile) formData.append('cv', fields.cvFile)
  return request<ApplyResult>(`/api/v1/public/jobs/${encodeURIComponent(slug)}/apply`, {
    method: 'POST',
    formData,
  })
}

export function getInterviewLanding(token: string): Promise<InterviewLanding> {
  return request<InterviewLanding>(`/api/v1/public/interviews/${encodeURIComponent(token)}`)
}

export function startInterview(token: string): Promise<InterviewLanding> {
  return request<InterviewLanding>(`/api/v1/public/interviews/${encodeURIComponent(token)}/start`, {
    method: 'POST',
  })
}

export function getInterviewStatus(token: string): Promise<InterviewLanding> {
  return request<InterviewLanding>(`/api/v1/public/interviews/${encodeURIComponent(token)}/status`)
}
