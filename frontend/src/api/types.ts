/**
 * TypeScript mirrors of the FastAPI response/request schemas in `api/schemas/`.
 *
 * These are hand-maintained rather than generated: the surface is small, and an
 * explicit mirror makes a backend contract change fail at `tsc` rather than at
 * runtime. If a backend schema changes, update the matching type here.
 */

/** Mirrors models/common.py PositionStatus. */
export type PositionStatus = 'draft' | 'active' | 'closed'

/** Mirrors models/common.py CandidateStatus. 'applied' and 'invited' are the
 *  auto-pipeline-only entry states between 'new' and 'queued' -- see the
 *  Python enum's docstring. */
export type CandidateStatus =
  | 'new'
  | 'applied'
  | 'queued'
  | 'invited'
  | 'screening_in_progress'
  | 'screened'
  | 'withdrawn'

/** Mirrors models/common.py QueueStatus: whether the background worker may
 *  claim this queue's items. Start/Pause/Resume are the only things that move it. */
export type QueueStatus = 'idle' | 'running' | 'paused'

/** Mirrors models/common.py QueueKind: a recruiter-built batch vs. the one
 *  queue the automated screening pipeline creates per published position. */
export type QueueKind = 'manual' | 'auto'

/** Mirrors models/common.py QueueItemStatus. Describes whether a screening
 *  conversation happened — never its outcome, and never a hiring decision. */
export type QueueItemStatus =
  | 'awaiting_candidate'
  | 'pending'
  | 'claimed'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'no_answer'
  | 'cancelled'

/** Mirrors models/common.py QuestionCategory. Required on every question -- the
 *  evaluator maps this to an evaluation category, so it is never inferred. */
export type QuestionCategory =
  | 'introduction'
  | 'candidate_background'
  | 'cv_project_validation'
  | 'technical'
  | 'problem_solving'
  | 'behavioral'
  | 'closing'

/** Mirrors models/common.py InterviewState. */
export type InterviewState = 'CREATED' | 'READY' | 'IN_PROGRESS' | 'COMPLETED' | 'EVALUATED'

export type EvaluationCategory =
  | 'relevant_experience'
  | 'technical_knowledge'
  | 'problem_solving'
  | 'communication'
  | 'behavioral_competencies'
  | 'job_requirement_coverage'

export type ScreeningOutcome = 'PASS' | 'FAIL' | 'NEEDS_REVIEW'

/** Mirrors models/common.py ExperienceLevel. */
export type ExperienceLevel =
  | 'Intern'
  | 'Junior'
  | 'Mid-Level'
  | 'Senior'
  | 'Lead'
  | 'Principal'
  | 'Executive'

export interface User {
  id: number
  email: string
  full_name: string
  role: string
  is_active: boolean
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface Position {
  id: number
  owner_id: number
  company_name: string
  title: string
  description: string | null
  experience_level: string | null
  pass_score_threshold: number | null
  rubric_profile: string | null
  status: PositionStatus
}

/** Mirrors models/common.py TemplateStatus: review state of a position's
 *  screening template (JD + question bank + agent persona + rubric taken
 *  together), separate from the candidate-level InterviewPlanStatus. */
export type TemplateStatus = 'draft' | 'approved'

/** Mirrors api/schemas/screening_config.py ScreeningConfigResponse. One row
 *  per position: the automated screening pipeline's automation settings plus
 *  its template approval / publish state. */
export interface ScreeningConfig {
  position_id: number
  template_status: TemplateStatus
  template_approved_at: string | null
  accept_public_applications: boolean
  auto_parse_cv: boolean
  auto_create_plan: boolean
  auto_create_invitation: boolean
  allow_immediate_start: boolean
  auto_email_invitation: boolean
  allow_cv_personalization: boolean
  invitation_ttl_hours: number
  reminder_offsets_hours: number[]
  max_applications_per_day: number | null
  require_phone: boolean
  application_notice: string | null
  public_slug: string | null
  public_url: string | null
}

/** Mirrors api/schemas/screening_config.py ScreeningSettingsUpdateRequest.
 *  accept_public_applications is deliberately absent -- only publish/unpublish
 *  may change it. */
export interface ScreeningConfigUpdate {
  auto_parse_cv?: boolean
  auto_create_plan?: boolean
  auto_create_invitation?: boolean
  allow_immediate_start?: boolean
  auto_email_invitation?: boolean
  allow_cv_personalization?: boolean
  invitation_ttl_hours?: number
  reminder_offsets_hours?: number[]
  max_applications_per_day?: number | null
  require_phone?: boolean
  application_notice?: string | null
}

/** Mirrors api/schemas/public.py PublicJobResponse: what an anonymous visitor
 *  to /jobs/{slug} may see. Never rubric_profile, pass_score_threshold,
 *  owner_id, questions, or candidate/evaluation data. */
export interface PublicJob {
  slug: string
  title: string
  company_name: string
  description: string | null
  experience_level: string | null
  require_phone: boolean
  application_notice: string | null
}

export interface PositionCreate {
  company_name: string
  title: string
  description?: string | null
  experience_level?: string | null
  pass_score_threshold?: number | null
  rubric_profile?: string | null
  status?: PositionStatus
}

export type PositionUpdate = Partial<PositionCreate>

export interface Question {
  id: number
  position_id: number
  category: QuestionCategory
  question: string
  order: number
  purpose: string | null
  expected_topics: string[]
  difficulty: string
  follow_up_allowed: boolean
}

export interface QuestionCreate {
  category: QuestionCategory
  question: string
  order: number
  purpose?: string | null
  expected_topics?: string[]
  difficulty?: string
  follow_up_allowed?: boolean
}

export type QuestionUpdate = Partial<Omit<QuestionCreate, 'order'>> & { order?: number }

/** Mirrors api/schemas/question.py SuggestedQuestionResponse. A proposal only --
 *  it has no database id because nothing is persisted until HR saves it. */
export interface SuggestedQuestion {
  suggestion_id: string
  category: QuestionCategory
  question: string
  purpose: string | null
  expected_topics: string[]
  difficulty: string
  follow_up_allowed: boolean
}

export interface Candidate {
  id: number
  position_id: number
  full_name: string
  email: string | null
  phone: string | null
  cv_text: string | null
  cv_filename: string | null
  status: CandidateStatus
}

export interface CandidateCreate {
  position_id: number
  full_name: string
  email?: string | null
  phone?: string | null
  cv_text?: string | null
  cv_filename?: string | null
}

export type CandidateUpdate = Partial<Omit<CandidateCreate, 'position_id'>> & {
  status?: CandidateStatus
}

export interface AgentConfig {
  id: number
  owner_id: number
  position_id: number | null
  name: string
  config: Record<string, unknown>
  is_active: boolean
}

export interface AgentConfigCreate {
  name: string
  position_id?: number | null
  config?: Record<string, unknown>
  is_active?: boolean
}

export interface AgentConfigUpdate {
  name?: string
  config?: Record<string, unknown>
  is_active?: boolean
}

/** Mirrors models/job.py JobAnalysis. */
export interface JobAnalysis {
  job_title: string
  experience_level: string | null
  required_skills: string[]
  nice_to_have_skills: string[]
  responsibilities: string[]
  technical_topics: string[]
  behavioral_competencies: string[]
  role_summary: string
}

/** Mirrors models/candidate.py CandidateAnalysis. */
export interface CandidateAnalysis {
  full_name: string | null
  skills: string[]
  technologies: string[]
  experience: string[]
  projects: string[]
  education: string[]
  important_cv_claims: string[]
  relevant_experience: string[]
  unclear_claims_to_validate: string[]
}

/** Mirrors models/candidate.py FitAnalysis. */
export interface FitAnalysis {
  strong_alignment_areas: string[]
  relevant_candidate_experience: string[]
  important_job_requirements: string[]
  skills_requiring_validation: string[]
  missing_information: string[]
  questions_to_investigate: string[]
}

/** Mirrors models/interview.py InterviewQuestion. */
export interface InterviewQuestion {
  id: string
  category: QuestionCategory
  question: string
  purpose: string
  expected_topics: string[]
  difficulty: string
  follow_up_allowed: boolean
}

export interface InterviewPlan {
  questions: InterviewQuestion[]
}

/** Mirrors models/common.py InterviewPlanStatus. */
export type InterviewPlanStatus = 'draft' | 'approved'

/** Mirrors models/common.py PlanQuestionSource: role baseline, AI-proposed, or hand-written. */
export type PlanQuestionSource = 'bank' | 'generated' | 'manual'

/** Mirrors api/schemas/interview_plan.py PlanQuestionResponse. */
export interface PlanQuestion {
  order: number
  category: QuestionCategory
  question: string
  purpose: string | null
  expected_topics: string[]
  difficulty: string
  follow_up_allowed: boolean
  source: PlanQuestionSource
}

/** What the client sends when saving; order comes from list position. */
export type PlanQuestionInput = Omit<PlanQuestion, 'order'>

export interface CandidateInterviewPlan {
  candidate_id: number
  status: InterviewPlanStatus
  questions: PlanQuestion[]
  generated_at: string | null
  approved_at: string | null
}

/** Mirrors models/agent_persona.py ConversationalStyle. */
export interface ConversationalStyle {
  use_candidate_name: boolean
  brief_acknowledgements: boolean
  allow_question_rephrasing: boolean
  natural_pauses: boolean
  allow_interruptions: boolean
}

/** Mirrors models/agent_persona.py VoiceAgentPersona. */
export interface VoiceAgentPersona {
  agent_name: string
  company_name: string
  ai_role_title: string
  language: string
  tone: string
  opening_script: string
  closing_script: string
  off_topic_redirection: string
  follow_up_style: string
  candidate_question_handling: string
  conversational_style: ConversationalStyle
}

/** Mirrors api/schemas/interview.py PrepareInterviewResponse. */
export interface PrepareInterviewResult {
  session_id: string
  state: InterviewState
  job_analysis: JobAnalysis
  candidate_analysis: CandidateAnalysis
  fit_analysis: FitAnalysis
  interview_plan: InterviewPlan
  llm_provider: string
  is_mock: boolean
}

/** Mirrors api/schemas/queue.py QueueResponse. */
export interface CallQueue {
  id: number
  position_id: number
  name: string
  status: QueueStatus
  kind: QueueKind
  created_at: string | null
  updated_at: string | null
}

/** Mirrors api/schemas/queue.py QueueItemResponse.
 *
 *  `claimed_by` and `lease_expires_at` are the worker's bookkeeping, surfaced so
 *  a recruiter can tell a call that is genuinely under way from one that is stuck. */
export interface QueueItem {
  id: number
  queue_id: number
  candidate_id: number
  status: QueueItemStatus
  attempts: number
  max_attempts: number
  claimed_by: string | null
  claimed_at: string | null
  lease_expires_at: string | null
  next_attempt_at: string | null
  last_error: string | null
  interview_session_id: string | null
  created_at: string | null
  updated_at: string | null
}

/** Mirrors api/schemas/queue.py QueueProgressResponse. Counts only — no names,
 *  scores, or screening outcomes cross this boundary. */
export interface QueueProgress {
  queue_id: number
  status: QueueStatus
  total: number
  counts: Record<QueueItemStatus, number>
  finished: number
  in_flight: number
}

export interface CategoryEvaluation {
  category: EvaluationCategory
  score: number | null
  sufficient_evidence: boolean
  reasoning: string
  evidence: string[]
  areas_to_validate: string[]
}

export interface InterviewTurn {
  question_id: string
  question: string
  category: QuestionCategory
  answer: string
  is_follow_up: boolean
  timestamp: string
}

/** Recruiter-facing projection of the persisted HR report. */
export interface ScreeningResult {
  session_id: string
  company: string
  role: string
  candidate_name: string | null
  overall_score: number | null
  evidence_coverage: number
  screening_outcome: ScreeningOutcome
  recommendation: string
  category_scores: CategoryEvaluation[]
  strengths: string[]
  areas_to_validate: string[]
  ai_summary: string
  human_follow_up_questions: string[]
  full_transcript: InterviewTurn[]
  llm_provider: string
  is_mock: boolean
  generated_at: string
}

/** Signed recruiter-created invitation; contains no LiveKit secret. */
export interface VoiceInvitation {
  invite_url: string
  expires_at: string
}

/** Short-lived candidate room credential returned only after invite validation. */
export interface VoiceRoomToken {
  token: string
  server_url: string
  room_name: string
  participant_identity: string
  participant_name: string
  agent_name: string
  company_name: string
}

export interface ConsentNotice {
  consent_required: boolean
  consent_version: string
  consent_text: string
}

export interface RecordingConsent {
  consented_at: string
  consent_version: string
  consent_text: string
}

export interface TranscriptSegment {
  sequence: number
  speaker: 'interviewer' | 'candidate'
  content: string
  spoken_at: string
}

export interface InterviewRecording {
  status: 'pending' | 'active' | 'completed' | 'failed' | 'aborted'
  /** Null when the recording failed, is still running, or has no public URL. */
  audio_url: string | null
  duration_seconds: number | null
  file_size_bytes: number | null
  started_at: string | null
  ended_at: string | null
  error: string | null
}

export interface InterviewMedia {
  interview_session_id: string
  recording: InterviewRecording | null
  transcript: TranscriptSegment[]
}

/** Mirrors models/common.py ApplicationState: where one candidate's
 *  self-service application stands in the automated pipeline. */
export type ApplicationState = 'received' | 'candidate_created' | 'plan_ready' | 'invited' | 'abandoned'

/** Mirrors api/schemas/public.py ApplyResponse. */
export interface ApplyResult {
  application_id: number
  /** Present only once the pipeline has issued a usable interview link --
   *  absent when an automation setting is off, in which case HR finishes by hand. */
  interview_token: string | null
}

/** Mirrors api/schemas/public.py InterviewLandingResponse's `stage` values. */
export type InterviewStage = 'not_started' | 'preparing' | 'ready' | 'completed' | 'failed'

/** Mirrors api/schemas/public.py InterviewLandingResponse. */
export interface InterviewLanding {
  position_title: string
  company_name: string
  candidate_first_name: string
  expires_at: string
  stage: InterviewStage
  /** Set only when stage === 'ready' — navigate the browser here to join. */
  voice_invite_url: string | null
}

/** Mirrors api/schemas/pipeline.py PipelineRowResponse. */
export interface PipelineRow {
  application_id: number
  candidate_id: number | null
  full_name: string
  email: string
  phone: string | null
  pipeline_state: ApplicationState
  cv_parse_error: string | null
  last_error: string | null
  created_at: string | null
}

/** Mirrors api/schemas/pipeline.py InvitationIssuedResponse. */
export interface InvitationIssued {
  expires_at: string
  interview_url: string
}
