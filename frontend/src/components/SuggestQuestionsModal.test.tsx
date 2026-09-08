import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as questionsApi from '@/api/questions'
import type { SuggestedQuestion } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { SuggestQuestionsModal } from './SuggestQuestionsModal'

const SUGGESTIONS: SuggestedQuestion[] = [
  {
    suggestion_id: 's-1',
    category: 'technical',
    question: 'Describe the retrieval pipeline you built.',
    purpose: 'Assess technical depth.',
    expected_topics: ['retrieval'],
    difficulty: 'medium',
    follow_up_allowed: true,
  },
  {
    suggestion_id: 's-2',
    category: 'behavioral',
    question: 'Tell me about a disagreement with a teammate.',
    purpose: 'Assess collaboration.',
    expected_topics: [],
    difficulty: 'medium',
    follow_up_allowed: true,
  },
]

function renderModal(overrides: Partial<Parameters<typeof SuggestQuestionsModal>[0]> = {}) {
  return renderWithProviders(
    <SuggestQuestionsModal
      open
      positionId={7}
      existingQuestionCount={0}
      onClose={overrides.onClose ?? vi.fn()}
      onAdded={overrides.onAdded ?? vi.fn()}
    />,
  )
}

describe('SuggestQuestionsModal', () => {
  it('generates suggestions and shows them for review', async () => {
    const suggest = vi.spyOn(questionsApi, 'suggestQuestions').mockResolvedValue(SUGGESTIONS)

    renderModal()
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))

    await waitFor(() => expect(suggest).toHaveBeenCalledWith(7, { numQuestions: 6 }))
    expect(await screen.findByText('Describe the retrieval pipeline you built.')).toBeInTheDocument()
    expect(screen.getByText('Tell me about a disagreement with a teammate.')).toBeInTheDocument()
  })

  it('saves nothing until HR confirms', async () => {
    vi.spyOn(questionsApi, 'suggestQuestions').mockResolvedValue(SUGGESTIONS)
    const addQuestion = vi.spyOn(questionsApi, 'addQuestion')

    renderModal()
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))
    await screen.findByText('Describe the retrieval pipeline you built.')

    expect(addQuestion).not.toHaveBeenCalled()
  })

  it('adds only the selected suggestions, preserving category and order', async () => {
    vi.spyOn(questionsApi, 'suggestQuestions').mockResolvedValue(SUGGESTIONS)
    const addQuestion = vi.spyOn(questionsApi, 'addQuestion').mockResolvedValue({
      id: 1,
      position_id: 7,
      category: 'technical',
      question: 'x',
      order: 0,
      purpose: null,
      expected_topics: [],
      difficulty: 'medium',
      follow_up_allowed: true,
    })
    const onAdded = vi.fn()

    renderModal({ onAdded })
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))
    await screen.findByText('Describe the retrieval pipeline you built.')

    // Everything starts selected; deselect the second so only one is saved.
    const checkboxes = screen.getAllByRole('checkbox')
    await userEvent.click(checkboxes[1]!)
    await userEvent.click(screen.getByRole('button', { name: /add 1 question$/i }))

    await waitFor(() => expect(addQuestion).toHaveBeenCalledTimes(1))
    expect(addQuestion).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ category: 'technical', order: 0 }),
    )
    expect(onAdded).toHaveBeenCalled()
  })

  it('continues numbering after the position’s existing questions', async () => {
    vi.spyOn(questionsApi, 'suggestQuestions').mockResolvedValue([SUGGESTIONS[0]!])
    const addQuestion = vi.spyOn(questionsApi, 'addQuestion').mockResolvedValue({
      id: 1,
      position_id: 7,
      category: 'technical',
      question: 'x',
      order: 3,
      purpose: null,
      expected_topics: [],
      difficulty: 'medium',
      follow_up_allowed: true,
    })

    renderWithProviders(
      <SuggestQuestionsModal
        open
        positionId={7}
        existingQuestionCount={3}
        onClose={vi.fn()}
        onAdded={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))
    await screen.findByText('Describe the retrieval pipeline you built.')
    await userEvent.click(screen.getByRole('button', { name: /add 1 question$/i }))

    await waitFor(() => expect(addQuestion).toHaveBeenCalled())
    expect(addQuestion.mock.calls[0]![1]).toMatchObject({ order: 3 })
  })

  it('clearly limits Position suggestions to role and Job Description context', async () => {
    const suggest = vi.spyOn(questionsApi, 'suggestQuestions').mockResolvedValue(SUGGESTIONS)

    renderModal()
    expect(screen.getByText('Job description only')).toBeInTheDocument()
    expect(screen.getByText(/only the role title, Job Description, and experience level/i)).toBeInTheDocument()
    expect(screen.getByText(/candidate profiles and CVs are never included/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/tailor to candidate/i)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))

    await waitFor(() => expect(suggest).toHaveBeenCalledWith(7, { numQuestions: 6 }))
  })

  it('surfaces a generation failure', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(questionsApi, 'suggestQuestions').mockRejectedValue(
      new ApiError(400, 'Position question suggestions must be role-generic'),
    )

    renderModal()
    await userEvent.click(screen.getByRole('button', { name: /generate suggestions/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/role-generic/i)
  })
})
