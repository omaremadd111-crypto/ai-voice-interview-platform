import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import * as agentConfigsApi from '@/api/agentConfigs'
import * as candidatesApi from '@/api/candidates'
import * as positionsApi from '@/api/positions'
import * as questionsApi from '@/api/questions'
import type { AgentConfig, Position, Question } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { PositionDetailPage } from './PositionDetailPage'

const POSITION: Position = {
  id: 7,
  owner_id: 1,
  company_name: 'Acme',
  title: 'Junior AI Engineer',
  description: null,
  experience_level: 'Junior',
  pass_score_threshold: 65,
  rubric_profile: null,
  status: 'active',
}

function question(overrides: Partial<Question> & Pick<Question, 'id' | 'order' | 'question'>): Question {
  return {
    position_id: 7,
    category: 'technical',
    purpose: null,
    expected_topics: [],
    difficulty: 'medium',
    follow_up_allowed: true,
    ...overrides,
  }
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/positions/:positionId" element={<PositionDetailPage />} />
    </Routes>,
    { route: '/positions/7' },
  )
}

/** Questions now live behind their own tab (Phase 5) instead of always being
 *  on screen -- tests that exercise them switch tabs first. */
async function openQuestionsTab() {
  await userEvent.click(await screen.findByRole('tab', { name: /questions/i }))
}

describe('PositionDetailPage', () => {
  it('lists questions in their stored order', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([
      question({ id: 1, order: 0, question: 'First question' }),
      question({ id: 2, order: 1, question: 'Second question' }),
    ])

    renderPage()
    await openQuestionsTab()

    const items = await screen.findAllByRole('listitem')
    const questionTexts = items
      .map((item) => item.textContent ?? '')
      .filter((text) => text.includes('question'))
    expect(questionTexts[0]).toContain('First question')
    expect(questionTexts[1]).toContain('Second question')
  })

  it('refuses to submit a question without an explicitly chosen category', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])
    const addQuestion = vi.spyOn(questionsApi, 'addQuestion')

    renderPage()
    await openQuestionsTab()

    await userEvent.click(await screen.findByRole('button', { name: /^add question$/i }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/^question/i), 'A new question?')
    await userEvent.click(within(dialog).getByRole('button', { name: /add question/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/category/i)
    expect(addQuestion).not.toHaveBeenCalled()
  })

  it('sends the full reordered id list when a question is moved', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([
      question({ id: 1, order: 0, question: 'First question' }),
      question({ id: 2, order: 1, question: 'Second question' }),
    ])
    const reorder = vi.spyOn(questionsApi, 'reorderQuestions').mockResolvedValue([])

    renderPage()
    await openQuestionsTab()

    const downButtons = await screen.findAllByRole('button', { name: /move question down/i })
    await userEvent.click(downButtons[0]!)

    expect(reorder).toHaveBeenCalledWith(7, [2, 1])
  })

  it('offers only reusable Position Question Bank categories', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])

    renderPage()
    await openQuestionsTab()
    await userEvent.click(await screen.findByRole('button', { name: /^add question$/i }))
    const dialog = await screen.findByRole('dialog')

    const categorySelect = within(dialog).getByLabelText(/category/i)
    const optionValues = within(categorySelect)
      .getAllByRole('option')
      .map((option) => (option as HTMLOptionElement).value)
      .filter((value) => value !== '')

    expect(optionValues).toEqual([
      'candidate_background',
      'technical',
      'problem_solving',
      'behavioral',
    ])
  })

  it('shows only the agent configuration scoped to this position, not one scoped elsewhere', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])
    const ownConfig: AgentConfig = {
      id: 1,
      owner_id: 1,
      position_id: 7,
      name: 'Junior AI Engineer screening',
      is_active: true,
      config: {
        agent_name: 'Aimy',
        company_name: 'Acme',
        ai_role_title: 'AI screening assistant',
        language: 'English',
        tone: 'professional and warm',
        opening_script: 'Hi, I am Aimy, an AI interviewer for Acme.',
        closing_script: 'Thanks for your time.',
        off_topic_redirection: '',
        follow_up_style: '',
        candidate_question_handling: '',
        conversational_style: {
          use_candidate_name: true,
          brief_acknowledgements: true,
          allow_question_rephrasing: true,
          natural_pauses: true,
          allow_interruptions: true,
        },
      },
    }
    const otherConfig: AgentConfig = { ...ownConfig, id: 2, position_id: 99, name: 'A different role' }
    vi.spyOn(agentConfigsApi, 'listAgentConfigs').mockResolvedValue([ownConfig, otherConfig])

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /agent/i }))

    expect(await screen.findByText('Junior AI Engineer screening')).toBeInTheDocument()
    expect(screen.queryByText('A different role')).not.toBeInTheDocument()
  })

  it('points to Agent Configuration when nothing is scoped to this position', async () => {
    vi.spyOn(positionsApi, 'getPosition').mockResolvedValue(POSITION)
    vi.spyOn(candidatesApi, 'listCandidates').mockResolvedValue([])
    vi.spyOn(questionsApi, 'listQuestions').mockResolvedValue([])
    vi.spyOn(agentConfigsApi, 'listAgentConfigs').mockResolvedValue([])

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /agent/i }))

    expect(
      await screen.findByText(/no configuration scoped to this position/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /go to agent configuration/i })).toHaveAttribute(
      'href',
      '/agent-config',
    )
  })
})
