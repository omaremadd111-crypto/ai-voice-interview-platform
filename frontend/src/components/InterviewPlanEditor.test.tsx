import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import * as plansApi from '@/api/interviewPlans'
import type { CandidateInterviewPlan, PlanQuestion } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { InterviewPlanEditor } from './InterviewPlanEditor'

function question(overrides: Partial<PlanQuestion> & Pick<PlanQuestion, 'order' | 'question'>): PlanQuestion {
  return {
    category: 'technical',
    purpose: null,
    expected_topics: [],
    difficulty: 'medium',
    follow_up_allowed: true,
    source: 'generated',
    ...overrides,
  }
}

const PLAN: CandidateInterviewPlan = {
  candidate_id: 42,
  status: 'draft',
  questions: [
    question({ order: 0, question: 'Baseline question.', source: 'bank', category: 'behavioral' }),
    question({ order: 1, question: 'CV-specific question.' }),
  ],
  generated_at: '2026-08-22T10:00:00Z',
  approved_at: null,
}

function render(
  plan: CandidateInterviewPlan | null,
  onPlanChange = vi.fn(),
  headerActions?: ReactNode,
) {
  renderWithProviders(
    <InterviewPlanEditor
      candidateId={42}
      plan={plan}
      onPlanChange={onPlanChange}
      headerActions={headerActions}
    />,
  )
  return onPlanChange
}

describe('InterviewPlanEditor', () => {
  it('offers generation when no plan exists yet', async () => {
    const generate = vi.spyOn(plansApi, 'generateInterviewPlan').mockResolvedValue(PLAN)
    const onPlanChange = render(null)

    await userEvent.click(screen.getByRole('button', { name: /generate interview plan/i }))

    await waitFor(() => expect(generate).toHaveBeenCalledWith(42))
    expect(onPlanChange).toHaveBeenCalledWith(PLAN)
  })

  it('shows where each question came from', () => {
    render(PLAN)
    expect(screen.getByText('Role baseline')).toBeInTheDocument()
    expect(screen.queryByText('AI-generated')).not.toBeInTheDocument()
  })

  it('navigates one focused question at a time and highlights the active tab', async () => {
    render(PLAN)

    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument()
    expect(screen.getByLabelText('Question 1')).toHaveValue('Baseline question.')
    expect(screen.queryByLabelText('Question 2')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /go to question 1/i })).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(screen.getByRole('button', { name: /previous question/i })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /next question/i }))

    expect(screen.getByText('Question 2 of 2')).toBeInTheDocument()
    expect(screen.getByLabelText('Question 2')).toHaveValue('CV-specific question.')
    expect(screen.getByText('AI-generated')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /go to question 2/i })).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(screen.getByRole('button', { name: /next question/i })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /go to question 1/i }))
    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument()
  })

  it('keeps unsaved edits when navigating between questions', async () => {
    render(PLAN)

    await userEvent.clear(screen.getByLabelText('Question 1'))
    await userEvent.type(screen.getByLabelText('Question 1'), 'Unsaved rewording.')
    await userEvent.click(screen.getByRole('button', { name: /next question/i }))
    expect(screen.getByLabelText('Question 2')).toHaveValue('CV-specific question.')

    await userEvent.click(screen.getByRole('button', { name: /previous question/i }))
    expect(screen.getByLabelText('Question 1')).toHaveValue('Unsaved rewording.')
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()
  })

  it('keeps save, approve, and prepare actions in the sticky plan toolbar', () => {
    render(PLAN, vi.fn(), <button type="button">Prepare interview</button>)

    const toolbar = screen.getByTestId('interview-plan-toolbar')
    expect(toolbar).toHaveClass('sticky')
    expect(within(toolbar).getByRole('button', { name: /save plan/i })).toBeVisible()
    expect(within(toolbar).getByRole('button', { name: /approve plan/i })).toBeVisible()
    expect(within(toolbar).getByRole('button', { name: /prepare interview/i })).toBeVisible()
  })

  it('saves edits as a whole ordered list', async () => {
    const save = vi.spyOn(plansApi, 'saveInterviewPlan').mockResolvedValue(PLAN)
    render(PLAN)

    const first = screen.getByLabelText('Question 1')
    await userEvent.clear(first)
    await userEvent.type(first, 'Reworded baseline.')
    await userEvent.click(screen.getByRole('button', { name: /save plan/i }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    const sent = save.mock.calls[0]![1]
    expect(sent[0]!.question).toBe('Reworded baseline.')
    expect(sent).toHaveLength(2)
    // order is derived from list position, so it is not sent per question.
    expect(sent[0]).not.toHaveProperty('order')
  })

  it('reorders locally and sends the new order on save', async () => {
    const save = vi.spyOn(plansApi, 'saveInterviewPlan').mockResolvedValue(PLAN)
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /move question 1 down/i }))
    await userEvent.click(screen.getByRole('button', { name: /save plan/i }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0]![1].map((q) => q.question)).toEqual([
      'CV-specific question.',
      'Baseline question.',
    ])
  })

  it('deletes a question locally before saving', async () => {
    const save = vi.spyOn(plansApi, 'saveInterviewPlan').mockResolvedValue(PLAN)
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /next question/i }))
    await userEvent.click(screen.getByRole('button', { name: /delete question 2/i }))
    await userEvent.click(screen.getByRole('button', { name: /save plan/i }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0]![1]).toHaveLength(1)
  })

  it('refuses to save a newly added question with no category', async () => {
    const save = vi.spyOn(plansApi, 'saveInterviewPlan')
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /add question/i }))
    await userEvent.click(screen.getByRole('button', { name: /save plan/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/needs both a category/i)
    expect(save).not.toHaveBeenCalled()
  })

  it('regenerates a single question', async () => {
    const regenerate = vi.spyOn(plansApi, 'regeneratePlanQuestion').mockResolvedValue(PLAN)
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /next question/i }))
    await userEvent.click(screen.getByRole('button', { name: /regenerate question 2/i }))

    await waitFor(() => expect(regenerate).toHaveBeenCalledWith(42, 1))
  })

  it('regenerates the whole plan', async () => {
    const generate = vi.spyOn(plansApi, 'generateInterviewPlan').mockResolvedValue(PLAN)
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /regenerate whole plan/i }))

    await waitFor(() => expect(generate).toHaveBeenCalledWith(42))
  })

  it('approves the plan', async () => {
    const approve = vi.spyOn(plansApi, 'approveInterviewPlan').mockResolvedValue({
      ...PLAN,
      status: 'approved',
      approved_at: '2026-08-22T11:00:00Z',
    })
    render(PLAN)

    await userEvent.click(screen.getByRole('button', { name: /approve plan/i }))

    await waitFor(() => expect(approve).toHaveBeenCalledWith(42))
  })

  it('blocks approval while there are unsaved edits', async () => {
    const approve = vi.spyOn(plansApi, 'approveInterviewPlan')
    render(PLAN)

    await userEvent.type(screen.getByLabelText('Question 1'), ' edited')

    expect(screen.getByRole('button', { name: /approve plan/i })).toBeDisabled()
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()
    expect(approve).not.toHaveBeenCalled()
  })

  it('marks an approved plan and disables re-approval', () => {
    render({ ...PLAN, status: 'approved', approved_at: '2026-08-22T11:00:00Z' })

    // "Approved" appears twice by design: the status badge and the now-inert button.
    expect(screen.getAllByText('Approved').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: /approved/i })).toBeDisabled()
    expect(screen.getByText(/will be used for the interview/i)).toBeInTheDocument()
  })

  it('discards local edits on request', async () => {
    render(PLAN)

    const first = screen.getByLabelText('Question 1')
    await userEvent.type(first, ' edited')
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /discard changes/i }))
    expect(screen.getByLabelText('Question 1')).toHaveValue('Baseline question.')
  })

  // Once the interview has run, the plan is the record of what the candidate
  // was screened against. It stays fully readable, but every control that
  // could rewrite it is absent -- not merely disabled.
  describe('read-only mode', () => {
    function renderReadOnly(plan: CandidateInterviewPlan | null, reason?: string) {
      const onPlanChange = vi.fn()
      renderWithProviders(
        <InterviewPlanEditor
          candidateId={42}
          plan={plan}
          onPlanChange={onPlanChange}
          readOnly
          readOnlyReason={reason}
        />,
      )
      return onPlanChange
    }

    it('keeps every question readable for audit', () => {
      renderReadOnly({ ...PLAN, status: 'approved', approved_at: '2026-08-22T11:00:00Z' })

      expect(screen.getByText('Baseline question.')).toBeInTheDocument()
      expect(screen.getByText('CV-specific question.')).toBeInTheDocument()
      expect(screen.getByText('Role baseline')).toBeInTheDocument()
      expect(screen.getByText('AI-generated')).toBeInTheDocument()
      // The approval it was actually run under stays visible as history.
      expect(screen.getByText('Approved')).toBeInTheDocument()
      expect(screen.getByText('Read-only')).toBeInTheDocument()
    })

    it('renders no control that could rewrite the plan', () => {
      renderReadOnly(PLAN)

      for (const name of [
        /save plan/i,
        /approve plan/i,
        /add question/i,
        /regenerate whole plan/i,
        /^regenerate question/i,
        /^delete question/i,
        /^move question/i,
        /generate interview plan/i,
        /discard changes/i,
      ]) {
        expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
      }
      // No editable surface at all: no free-text fields and no category or
      // difficulty selects to change how an answer would be evaluated.
      expect(screen.queryAllByRole('textbox')).toHaveLength(0)
      expect(screen.queryAllByRole('combobox')).toHaveLength(0)
    })

    it('explains why the plan is locked', () => {
      renderReadOnly(PLAN, 'This interview is complete.')
      expect(screen.getByText('This interview is complete.')).toBeInTheDocument()
    })

    it('never offers generation when an interview ran with no plan on record', () => {
      const generate = vi.spyOn(plansApi, 'generateInterviewPlan')
      renderReadOnly(null)

      expect(screen.getByText(/no interview plan on record/i)).toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: /generate interview plan/i }),
      ).not.toBeInTheDocument()
      expect(generate).not.toHaveBeenCalled()
    })
  })
})
