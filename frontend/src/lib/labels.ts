import type {
  CandidateStatus,
  EvaluationCategory,
  QueueItemStatus,
  QueueStatus,
  QuestionCategory,
} from '@/api/types'

/** Human-readable labels for the backend's enum values. Kept out of the badge
 *  components so those files export only components (React Fast Refresh). */

export const CANDIDATE_STATUS_LABEL: Record<CandidateStatus, string> = {
  new: 'New',
  applied: 'Applied',
  queued: 'Queued',
  invited: 'Invited',
  screening_in_progress: 'Screening in progress',
  screened: 'Screened',
  withdrawn: 'Withdrawn',
}

export const QUESTION_CATEGORY_LABEL: Record<QuestionCategory, string> = {
  introduction: 'Introduction',
  candidate_background: 'Background',
  cv_project_validation: 'CV validation',
  technical: 'Technical',
  problem_solving: 'Problem solving',
  behavioral: 'Behavioral',
  closing: 'Closing',
}

export const EVALUATION_CATEGORY_LABEL: Record<EvaluationCategory, string> = {
  relevant_experience: 'Relevant experience',
  technical_knowledge: 'Technical knowledge',
  problem_solving: 'Problem solving',
  communication: 'Communication',
  behavioral_competencies: 'Behavioral competencies',
  job_requirement_coverage: 'Job requirement coverage',
}

export const QUEUE_STATUS_LABEL: Record<QueueStatus, string> = {
  idle: 'Not started',
  running: 'Running',
  paused: 'Paused',
}

/** Deliberately neutral wording. A queue item describes whether a screening
 *  conversation happened, never how the candidate did — "No answer" means we
 *  could not reach them, and "Failed" means something broke on our side. */
export const QUEUE_ITEM_STATUS_LABEL: Record<QueueItemStatus, string> = {
  awaiting_candidate: 'Waiting for candidate',
  pending: 'Waiting',
  claimed: 'Starting',
  in_progress: 'Screening in progress',
  completed: 'Screening completed',
  failed: 'Could not complete',
  no_answer: 'No answer',
  cancelled: 'Cancelled',
}
