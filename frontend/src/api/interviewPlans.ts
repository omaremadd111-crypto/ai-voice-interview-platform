import { request } from './client'
import type { CandidateInterviewPlan, PlanQuestionInput } from './types'

/** Returns null when the candidate has no plan yet (the backend answers 404). */
export async function getInterviewPlan(
  candidateId: number,
): Promise<CandidateInterviewPlan | null> {
  const { ApiError } = await import('./client')
  try {
    return await request<CandidateInterviewPlan>(`/api/v1/candidates/${candidateId}/interview-plan`)
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) return null
    throw caught
  }
}

/** Rebuilds the plan from the position's question bank plus the candidate's CV.
 *  Replaces any existing plan and returns it to draft. */
export function generateInterviewPlan(
  candidateId: number,
  numQuestions = 8,
): Promise<CandidateInterviewPlan> {
  return request<CandidateInterviewPlan>(
    `/api/v1/candidates/${candidateId}/interview-plan/generate`,
    { method: 'POST', json: { num_questions: numQuestions } },
  )
}

/** Saves the complete ordered list — edit, add, delete and reorder in one call. */
export function saveInterviewPlan(
  candidateId: number,
  questions: PlanQuestionInput[],
): Promise<CandidateInterviewPlan> {
  return request<CandidateInterviewPlan>(`/api/v1/candidates/${candidateId}/interview-plan`, {
    method: 'PUT',
    json: { questions },
  })
}

export function regeneratePlanQuestion(
  candidateId: number,
  index: number,
): Promise<CandidateInterviewPlan> {
  return request<CandidateInterviewPlan>(
    `/api/v1/candidates/${candidateId}/interview-plan/questions/${index}/regenerate`,
    { method: 'POST' },
  )
}

export function approveInterviewPlan(candidateId: number): Promise<CandidateInterviewPlan> {
  return request<CandidateInterviewPlan>(
    `/api/v1/candidates/${candidateId}/interview-plan/approve`,
    { method: 'POST' },
  )
}
