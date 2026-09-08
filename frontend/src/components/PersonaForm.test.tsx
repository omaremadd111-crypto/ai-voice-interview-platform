import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { VoiceAgentPersona } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { PersonaForm } from './PersonaForm'

const PERSONA: VoiceAgentPersona = {
  agent_name: 'Aimy',
  company_name: 'FlairsTech',
  ai_role_title: 'AI screening assistant',
  language: 'English',
  tone: 'professional and warm',
  opening_script:
    "Hi {candidate_first_name}, I'm Aimy, FlairsTech's AI screening assistant. I'll be conducting your initial interview today.",
  closing_script: 'Thank you for your time. A recruiter will be in touch.',
  off_topic_redirection: 'Acknowledge briefly, then return to the question.',
  follow_up_style: 'One focused follow-up when an answer is thin.',
  candidate_question_handling: 'Answer role questions; defer decisions to a recruiter.',
  conversational_style: {
    use_candidate_name: true,
    brief_acknowledgements: true,
    allow_question_rephrasing: true,
    natural_pauses: true,
    allow_interruptions: true,
  },
}

function render(onChange = vi.fn()) {
  renderWithProviders(<PersonaForm persona={PERSONA} onChange={onChange} />)
  return onChange
}

describe('PersonaForm', () => {
  it('shows the core identity fields', () => {
    render()
    expect(screen.getByLabelText(/agent name/i)).toHaveValue('Aimy')
    expect(screen.getByLabelText(/company name/i)).toHaveValue('FlairsTech')
    expect(screen.getByLabelText(/ai role/i)).toHaveValue('AI screening assistant')
    expect(screen.getByLabelText(/language/i)).toHaveValue('English')
    expect(screen.getByLabelText(/^tone/i)).toHaveValue('professional and warm')
  })

  it('shows the opening and closing scripts', () => {
    render()
    expect(screen.getByLabelText(/opening script/i)).toHaveValue(PERSONA.opening_script)
    expect(screen.getByLabelText(/closing script/i)).toHaveValue(PERSONA.closing_script)
  })

  it('tells HR the opening must disclose the AI', () => {
    render()
    expect(screen.getByText(/must state that the interviewer is an ai/i)).toBeInTheDocument()
  })

  it('reports edits to the agent name', async () => {
    const onChange = render()
    await userEvent.type(screen.getByLabelText(/agent name/i), '!')
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ agent_name: 'Aimy!' }))
  })

  it('hides conversation handling until asked for', async () => {
    render()
    expect(screen.queryByLabelText(/off-topic redirection/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /show conversation handling/i }))

    expect(screen.getByLabelText(/off-topic redirection/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/follow-up style/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/handling candidate questions/i)).toBeInTheDocument()
  })

  it('exposes every conversational-style switch the voice agent will need', async () => {
    render()
    await userEvent.click(screen.getByRole('button', { name: /show conversation handling/i }))

    for (const label of [
      /use the candidate.s name/i,
      /brief acknowledgements/i,
      /rephrase when asked/i,
      /natural pauses/i,
      /allow interruptions/i,
    ]) {
      expect(screen.getByLabelText(label)).toBeChecked()
    }
  })

  it('reports a toggled conversational-style flag', async () => {
    const onChange = render()
    await userEvent.click(screen.getByRole('button', { name: /show conversation handling/i }))
    await userEvent.click(screen.getByLabelText(/allow interruptions/i))

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        conversational_style: expect.objectContaining({ allow_interruptions: false }),
      }),
    )
  })

  it('offers no control for prohibited inference settings', async () => {
    render()
    await userEvent.click(screen.getByRole('button', { name: /show conversation handling/i }))

    const body = document.body.textContent?.toLowerCase() ?? ''
    for (const prohibited of ['emotion', 'accent', 'personality', 'biometric', 'facial']) {
      expect(body).not.toContain(prohibited)
    }
  })
})
