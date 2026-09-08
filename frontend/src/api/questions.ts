import { request } from './client'
import type { Question, QuestionCreate, QuestionUpdate, SuggestedQuestion } from './types'

export function listQuestions(positionId: number): Promise<Question[]> {
  return request<Question[]>(`/api/v1/positions/${positionId}/questions`)
}

export function addQuestion(positionId: number, payload: QuestionCreate): Promise<Question> {
  return request<Question>(`/api/v1/positions/${positionId}/questions`, {
    method: 'POST',
    json: payload,
  })
}

export function updateQuestion(questionId: number, payload: QuestionUpdate): Promise<Question> {
  return request<Question>(`/api/v1/questions/${questionId}`, { method: 'PATCH', json: payload })
}

export function deleteQuestion(questionId: number): Promise<void> {
  return request<void>(`/api/v1/questions/${questionId}`, { method: 'DELETE' })
}

/**
 * Ask the backend for reusable Position Question Bank suggestions derived only
 * from the role/JD. Candidate-specific generation belongs to Interview Plans.
 * Nothing is persisted until HR keeps a suggestion via addQuestion().
 */
export function suggestQuestions(
  positionId: number,
  options: { numQuestions?: number } = {},
): Promise<SuggestedQuestion[]> {
  return request<SuggestedQuestion[]>(`/api/v1/positions/${positionId}/questions/suggest`, {
    method: 'POST',
    json: {
      num_questions: options.numQuestions ?? 6,
    },
  })
}

/** Sends the full ordered id list; the backend requires it to match the position's
 *  existing question set exactly and applies the reorder atomically. */
export function reorderQuestions(positionId: number, questionIds: number[]): Promise<Question[]> {
  return request<Question[]>(`/api/v1/positions/${positionId}/questions/reorder`, {
    method: 'PUT',
    json: { question_ids: questionIds },
  })
}
