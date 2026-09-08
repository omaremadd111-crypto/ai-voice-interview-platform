import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import * as candidatesApi from '@/api/candidates'
import * as interviewPlansApi from '@/api/interviewPlans'
import * as interviewsApi from '@/api/interviews'
import * as positionsApi from '@/api/positions'
import * as questionsApi from '@/api/questions'
import type {
  Candidate,
  CandidateInterviewPlan,
  Position,
  PrepareInterviewResult,
  Question,
  ScreeningResult,
} from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { CandidateDetailPage } from './CandidateDetailPage'

const POSITION: Position = {
  id: 7,
  owner_id: 1,
  company_name: 'Acme',
  title: 'Junior AI Engineer',
  description: 'Build RAG systems.',
  experience_level: 'Junior',
  pass_score_threshold: 65,
  rubric_profile: 'technical',
  status: 'active',
}

const CANDIDATE: Candidate = {
  id: 42,
  position_id: 7,
  full_name: 'Jordan Rivera',
  email: 'jordan@example.com',
  phone: null,
  cv_text: null,
  cv_filename: null,
  status: 'new',
}

const QUESTION: Question = {
  id: 3,
  position_id: 7,
  category: 'technical',
  question: 'Explain your RAG architecture.',
  order: 0,
  purpose: 'Assess depth.',
  expected_topics: ['retrieval'],
  difficulty: 'medium',
  follow_up_allowed: true,
}

const CANDIDATE_PLAN: CandidateInterviewPlan = {
  candidate_id: 42,
  status: 'draft',
  questions: [
    {
      order: 0,
      category: 'technical',
      question: 'Explain your RAG architecture.',
      purpose: 'Assess depth.',
      expected_topics: ['retrieval'],
      difficulty: 'medium',
      follow_up_allowed: true,
      source: 'bank',
    },
  ],
  generated_at: '2026-08-23T10:00:00Z',
  approved_at: null,
}

const CANDIDATE_PLAN_APPROVED: CandidateInterviewPlan = {
  ...CANDIDATE_PLAN,
  status: 'approved',
  approved_at: '2026-08-23T11:00:00Z',
}

const PREPARED: PrepareInterviewResult = {
  session_id: 'session-abc',
  state: 'CREATED',
  llm_provider: 'mock',
  is_mock: true,
  job_analysis: {
    job_title: 'Junior AI Engineer',
    experience_level: 'Junior',
    required_skills: ['Python'],
    nice_to_have_skills: [],
    responsibilities: [],
    technical_topics: ['retrieval'],
    behavioral_competencies: [],
    role_summary: 'Builds retrieval-augmented systems.',
  },
  candidate_analysis: {
    full_name: 'Jordan Rivera',
    skills: ['Python'],
    technologies: [],
    experience: [],
    projects: [],
    education: [],
    important_cv_claims: [],
    relevant_experience: [],
    unclear_claims_to_validate: [],
  },
  fit_analysis: {
    strong_alignment_areas: [],
    relevant_candidate_experience: [],
    important_job_requirements: [],
    skills_requiring_validation: [],
    missing_information: [],
    questions_to_investigate: [],
  },
  interview_plan: {
    questions: [
      {
        id: 'position-intro',
        category: 'introduction',
        question: 'Could you introduce yourself?',
        purpose: 'Warm-up.',
        expected_topics: [],
        difficulty: 'easy',
        follow_up_allowed: false,
      },
      {
        id: 'position-q-1',
        category: 'technical',
        question: 'Explain your RAG architecture.',
        purpose: 'Assess depth.',
        expected_topics: ['retrieval'],
        difficulty: 'medium',
        follow_up_allowed: true,
      },
    ],
  },
}

const RESULT: ScreeningResult = {
  session_id: 'session-report',
  company: 'Acme',
  role: 'Junior AI Engineer',
  candidate_name: 'Jordan Rivera',
  overall_score: null,
  evidence_coverage: 0.3,
  screening_outcome: 'NEEDS_REVIEW',
  recommendation: 'Insufficient evidence from this interview',
  category_scores: [
    {
      category: 'technical_knowledge',
      score: null,
      sufficient_evidence: false,
      reasoning: 'Insufficient evidence',
      evidence: [],
      areas_to_validate: ['Technical depth'],
    },
  ],
  strengths: ['Clearly described prior responsibilities.'],
  areas_to_validate: ['Technical depth'],
  ai_summary: 'The interview covered one of the planned evidence areas.',
  human_follow_up_questions: ['Could you provide a concrete example of technical depth?'],
  full_transcript: [
    {
      question_id: 'position-q-1',
      question: 'Explain your RAG architecture.',
      category: 'technical',
      answer: 'I used a vector index with a Python API.',
      is_follow_up: false,
      timestamp: '2026-08-23T10:00:00Z',
    },
  ],
  llm_provider: 'mock',
  is_mock: true,
  generated_at: '2026-08-23T10:01:00Z',
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/candidates/:candidateId" element={<CandidateDetailPage />} />
    </Routes>,
    { route: '/candidates/42' },
  )
}

/** The interview plan, recording/transcript, and evaluation content now live
 *  behind tabs (Phase 5) instead of always being on screen -- tests that
 *  exercise them switch tabs first, same as a recruiter would click. */
async function openTab(name: RegExp) {
  await userEvent.click(await screen.findByRole('tab', { name }))
}

describe('CandidateDetailPage', () => {
  it('prepares an interview and renders the returned plan', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    // Distinct question text from PREPARED's fixture below -- this plan and
    // the prepared result now render at the same time (the persisted-plan
    // editor stays mounted above InterviewPlanPanel), so sharing text would
    // make "Explain your RAG architecture." ambiguous between the two.
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue({
      ...CANDIDATE_PLAN_APPROVED,
      questions: [{ ...CANDIDATE_PLAN_APPROVED.questions[0]!, question: 'Describe your testing philosophy.' }],
    })
    const prepare = vi.spyOn(interviewsApi, 'prepareInterview').mockResolvedValue(PREPARED)

    renderPage()

    await screen.findByText('Jordan Rivera')
    await openTab(/interview plan/i)
    const emptyState = screen.getByTestId('prepared-interview-empty-state')
    await userEvent.click(within(emptyState).getByRole('button', { name: /prepare interview/i }))

    await waitFor(() => expect(prepare).toHaveBeenCalledWith(7, 42))
    expect(await screen.findByText(/interview plan · 2 questions/i)).toBeInTheDocument()
    expect(screen.getByText('Could you introduce yourself?')).toBeInTheDocument()
    expect(screen.getByText('Explain your RAG architecture.')).toBeInTheDocument()
    expect(screen.queryByTestId('prepared-interview-empty-state')).not.toBeInTheDocument()
  })

  it('renders a compact content-sized prepared-interview empty state', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)

    renderPage()

    await openTab(/interview plan/i)
    const emptyState = await screen.findByTestId('prepared-interview-empty-state')
    expect(within(emptyState).getByText('No interview prepared yet')).toBeVisible()
    expect(within(emptyState).getByText(/current plan and available CV details/i)).toBeVisible()
    expect(within(emptyState).getByRole('button', { name: /prepare interview/i })).toBeVisible()
    // One in this empty-state card, one in the interview-plan editor's own
    // sticky toolbar above it -- both exist once an approved plan is in play.
    expect(screen.getAllByRole('button', { name: /prepare interview/i })).toHaveLength(2)
    expect(emptyState).not.toHaveClass('h-full')
    expect(emptyState.className).not.toMatch(/(?:^|\s)min-h-/)
  })

  it('keeps Prepare interview in the sticky interview-plan toolbar', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN)

    renderPage()

    await openTab(/interview plan/i)
    const toolbar = await screen.findByTestId('interview-plan-toolbar')
    expect(toolbar).toHaveClass('sticky')
    expect(within(toolbar).getByRole('button', { name: /prepare interview/i })).toBeVisible()
    expect(within(toolbar).getByRole('button', { name: /save plan/i })).toBeVisible()
    expect(within(toolbar).getByRole('button', { name: /approve plan/i })).toBeVisible()
    expect(screen.getAllByRole('button', { name: /prepare interview/i })).toHaveLength(2)
  })

  it('labels a mock-mode plan so it is never mistaken for a real model response', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
    vi.spyOn(interviewsApi, 'prepareInterview').mockResolvedValue(PREPARED)

    renderPage()
    await openTab(/interview plan/i)
    const buttons = await screen.findAllByRole('button', { name: /prepare interview/i })
    await userEvent.click(buttons[0]!)

    expect(await screen.findByText(/demo \/ mock mode/i)).toBeInTheDocument()
  })

  it('blocks preparation and explains why when the position has no questions', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
    const prepare = vi.spyOn(interviewsApi, 'prepareInterview')

    renderPage()

    expect(await screen.findByText(/no interview questions yet/i)).toBeInTheDocument()
    await openTab(/interview plan/i)
    const buttons = await screen.findAllByRole('button', { name: /prepare interview/i })
    expect(buttons[0]).toBeDisabled()
    // Shown next to both the toolbar button and the empty-state card's own
    // button, so it's visible wherever the recruiter's eye lands.
    expect(
      screen.getAllByText(/add interview questions to this position first/i).length,
    ).toBeGreaterThan(0)
    expect(prepare).not.toHaveBeenCalled()
  })

  it('shows the backend error when preparation fails', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
    vi.spyOn(interviewsApi, 'prepareInterview').mockRejectedValue(
      new ApiError(400, 'Position 7 has no authored questions'),
    )

    renderPage()
    await openTab(/interview plan/i)
    const buttons = await screen.findAllByRole('button', { name: /prepare interview/i })
    await userEvent.click(buttons[0]!)

    expect(await screen.findByRole('alert')).toHaveTextContent('has no authored questions')
  })

  describe('Prepare interview gating', () => {
    it('shows only "Generate interview plan" and no Prepare action when no plan exists yet', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(null)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(null)

      renderPage()
      await openTab(/interview plan/i)

      expect(
        await screen.findByRole('button', { name: /generate interview plan/i }),
      ).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /prepare interview/i })).not.toBeInTheDocument()
      expect(screen.queryByTestId('prepared-interview-empty-state')).not.toBeInTheDocument()
    })

    it('disables every Prepare interview control, with a visible reason, while the plan is a draft', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(null)

      renderPage()
      await openTab(/interview plan/i)

      const buttons = await screen.findAllByRole('button', { name: /prepare interview/i })
      expect(buttons.length).toBeGreaterThan(0)
      for (const button of buttons) expect(button).toBeDisabled()
      expect(
        screen.getAllByText(/approve the interview plan before preparing the interview/i).length,
      ).toBeGreaterThan(0)
      // The draft plan is still fully visible for review and editing.
      expect(screen.getByText('Explain your RAG architecture.')).toBeInTheDocument()
      expect(screen.getByText(/draft — not yet approved/i)).toBeInTheDocument()
    })

    it('enables every Prepare interview control once the plan is approved', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(null)

      renderPage()
      await openTab(/interview plan/i)

      const buttons = await screen.findAllByRole('button', { name: /prepare interview/i })
      expect(buttons.length).toBeGreaterThan(0)
      for (const button of buttons) expect(button).toBeEnabled()
      expect(
        screen.queryByText(/approve the interview plan before preparing the interview/i),
      ).not.toBeInTheDocument()
    })

    it('lays out the Plan -> Approve -> Prepare -> Queue/Interview sequence in the Interview Plan tab', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(null)

      renderPage()
      await openTab(/interview plan/i)

      // "Plan" also appears in the page-level stage tracker above the tabs,
      // so this checks the sequence-unique labels; together with "Plan"
      // appearing at least twice, that confirms this tab has its own copy.
      expect(await screen.findByText('Approve')).toBeInTheDocument()
      expect(screen.getByText('Prepare')).toBeInTheDocument()
      expect(screen.getByText('Queue / Interview')).toBeInTheDocument()
      expect(screen.getAllByText('Plan').length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows the CV upload panel with a "No CV" state when the candidate has none', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])

    renderPage()

    expect(await screen.findByRole('heading', { name: /candidate cv/i })).toBeInTheDocument()
    expect(screen.getByText('No CV')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^upload cv$/i })).toBeInTheDocument()
  })

  it('shows the uploaded filename when the candidate already has a CV', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue({
      ...CANDIDATE,
      cv_text: 'Extracted CV text.',
      cv_filename: 'priya_cv.pdf',
    })
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])

    renderPage()

    expect(await screen.findByText('priya_cv.pdf')).toBeInTheDocument()
    expect(screen.getByText('Uploaded')).toBeInTheDocument()
  })

  it('shows the persisted HR report and suppresses a misleading low-evidence score', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue({ ...CANDIDATE, status: 'screened' })
    const getResult = vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(RESULT)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])

    renderPage()

    await waitFor(() => expect(getResult).toHaveBeenCalledWith(42))
    await openTab(/^evaluation$/i)
    expect(await screen.findByRole('heading', { name: /hr interview report/i })).toBeInTheDocument()
    expect(screen.getAllByText('NEEDS_REVIEW').length).toBeGreaterThan(0)
    expect(screen.getByText('Not scored')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
    expect(screen.getByText('Technical knowledge')).toBeInTheDocument()
    expect(screen.getByText('Clearly described prior responsibilities.')).toBeInTheDocument()
    expect(screen.getAllByText('Technical depth').length).toBeGreaterThan(0)
    expect(screen.getByText(/interview covered one of the planned evidence areas/i)).toBeInTheDocument()
    expect(screen.getByText(/could you provide a concrete example/i)).toBeInTheDocument()
    expect(screen.getByText(/i used a vector index with a python api/i)).toBeInTheDocument()
  })

  it('shows the stage tracker pointing at the next actionable step for a brand-new candidate', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(CANDIDATE)
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(null)
    vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(null)

    renderPage()

    const tracker = await screen.findByRole('list', { name: /candidate stage/i })
    expect(within(tracker).getByText('Added')).toBeInTheDocument()
    expect(within(tracker).getByText('Decision')).toBeInTheDocument()
    // No CV, no plan yet: nothing has happened past "Added", so the plan is
    // the next actionable step.
    expect(screen.getByText(/generate or write an interview plan/i)).toBeInTheDocument()
  })

  // A completed or in-flight interview turns the whole Interview Plan tab into
  // an audit view. The plan stays visible; every pre-interview action that
  // would rewrite what the candidate was screened against is withdrawn.
  describe('once the interview has run', () => {
    const SCREENED: Candidate = {
      ...CANDIDATE,
      status: 'screened',
      cv_text: 'Extracted CV text.',
      cv_filename: 'jordan-cv.pdf',
    }

    function mockPage(candidate: Candidate, result: ScreeningResult | null) {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(candidate)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(result)
    }

    it('locks the plan and withdraws every preparation action after completion', async () => {
      const prepare = vi.spyOn(interviewsApi, 'prepareInterview')
      mockPage(SCREENED, RESULT)

      renderPage()
      await openTab(/interview plan/i)

      // Still fully readable for audit.
      expect(await screen.findByTestId('interview-plan-readonly')).toBeInTheDocument()
      expect(screen.getByText('Explain your RAG architecture.')).toBeInTheDocument()

      expect(screen.queryByRole('button', { name: /prepare interview/i })).not.toBeInTheDocument()
      expect(screen.queryByTestId('prepared-interview-empty-state')).not.toBeInTheDocument()
      expect(screen.queryByText(/no interview prepared yet/i)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /regenerate whole plan/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /add question/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /save plan/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /approve plan/i })).not.toBeInTheDocument()
      expect(screen.getByText(/this interview is complete/i)).toBeInTheDocument()
      expect(prepare).not.toHaveBeenCalled()
    })

    it('locks the plan as soon as the interview starts, before any report exists', async () => {
      mockPage({ ...SCREENED, status: 'screening_in_progress' }, null)

      renderPage()
      await openTab(/interview plan/i)

      expect(await screen.findByTestId('interview-plan-readonly')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /prepare interview/i })).not.toBeInTheDocument()
      expect(screen.getByText(/this interview has already started/i)).toBeInTheDocument()
    })

    it('drops the "add questions before preparing" prompt once the interview has run', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(SCREENED)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      // The position's question bank was emptied after the interview ran.
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(CANDIDATE_PLAN_APPROVED)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(RESULT)

      renderPage()
      await screen.findByText('Jordan Rivera')

      expect(screen.queryByText(/before preparing an interview/i)).not.toBeInTheDocument()
    })

    it('keeps the screened CV readable but no longer replaceable', async () => {
      mockPage(SCREENED, RESULT)

      renderPage()
      await screen.findByText('Jordan Rivera')

      // Readable: filename and extracted text stay on the page.
      expect(screen.getByText('jordan-cv.pdf')).toBeInTheDocument()
      expect(screen.getByText(/view extracted text/i)).toBeInTheDocument()
      expect(screen.getByText(/the cv this candidate was screened against/i)).toBeInTheDocument()

      expect(screen.queryByRole('button', { name: /replace cv/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /remove cv/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /paste cv text instead/i })).not.toBeInTheDocument()
      expect(
        screen.queryByText(/used with the job description to generate/i),
      ).not.toBeInTheDocument()
    })

    it('reports where the candidate actually is when a completed interview has no plan row', async () => {
      vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue(SCREENED)
      vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
      vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
      vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue(null)
      vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(RESULT)

      renderPage()
      await screen.findByText('Jordan Rivera')

      // Never "go generate a plan" for a screening that already happened.
      expect(screen.queryByText(/generate or write an interview plan/i)).not.toBeInTheDocument()
      expect(screen.getByText(/screening complete — review the evaluation below/i)).toBeInTheDocument()

      await openTab(/interview plan/i)
      expect(await screen.findByTestId('interview-plan-readonly-empty')).toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: /generate interview plan/i }),
      ).not.toBeInTheDocument()
    })
  })

  it('marks the whole journey complete and shows the recorded outcome once a candidate is evaluated', async () => {
    vi.spyOn(candidatesApi, 'getCandidate').mockResolvedValue({
      ...CANDIDATE,
      status: 'screened',
      cv_text: 'Extracted CV text.',
    })
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([QUESTION])
    vi.spyOn(interviewPlansApi, 'getInterviewPlan').mockResolvedValue({
      ...CANDIDATE_PLAN,
      status: 'approved',
      approved_at: '2026-08-23T11:00:00Z',
    })
    vi.spyOn(candidatesApi, 'getCandidateResult').mockResolvedValue(RESULT)

    renderPage()

    await screen.findByText('Jordan Rivera')
    expect(await screen.findByText(/screening complete — review the evaluation below/i)).toBeInTheDocument()
    // The Overview tab's summary badge shows the same recorded screening
    // outcome the Decision stage is derived from, without switching tabs.
    expect(screen.getByText('NEEDS_REVIEW')).toBeInTheDocument()
  })
})
