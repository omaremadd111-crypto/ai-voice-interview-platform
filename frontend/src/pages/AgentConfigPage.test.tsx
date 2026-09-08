import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as agentConfigsApi from '@/api/agentConfigs'
import * as positionsApi from '@/api/positions'
import type { AgentConfig, VoiceAgentPersona } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { AgentConfigPage } from './AgentConfigPage'

const PERSONA: VoiceAgentPersona = {
  agent_name: 'Aimy',
  company_name: 'Acme',
  ai_role_title: 'AI screening assistant',
  language: 'English',
  tone: 'professional and warm',
  opening_script: 'Hi {candidate_first_name}, I am Aimy, an AI interviewer for Acme.',
  closing_script: 'Thanks for your time, {candidate_first_name}.',
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
}

const CONFIG: AgentConfig = {
  id: 1,
  owner_id: 1,
  position_id: null,
  name: 'Aimy — default',
  is_active: true,
  config: PERSONA as unknown as Record<string, unknown>,
}

function stubEmptyLists() {
  vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([])
}

describe('AgentConfigPage', () => {
  it('lists existing configurations with a persona summary', async () => {
    stubEmptyLists()
    vi.spyOn(agentConfigsApi, 'listAgentConfigs').mockResolvedValue([CONFIG])

    renderWithProviders(<AgentConfigPage />)

    expect(await screen.findByText('Aimy — default')).toBeInTheDocument()
    expect(screen.getByText(/Aimy · AI screening assistant/)).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('previews the opening and closing scripts with a sample name in place of the placeholder', async () => {
    stubEmptyLists()
    vi.spyOn(agentConfigsApi, 'listAgentConfigs').mockResolvedValue([])
    vi.spyOn(agentConfigsApi, 'getPersonaTemplate').mockResolvedValue(PERSONA)

    renderWithProviders(<AgentConfigPage />)

    await userEvent.click(await screen.findByRole('button', { name: /new configuration/i }))
    const dialog = await screen.findByRole('dialog')

    // The preview substitutes a sample name -- the literal placeholder itself
    // is never shown there (PersonaForm's own field hint still mentions it,
    // which is fine; this checks the preview text specifically).
    expect(within(dialog).getByText(/Hi Alex, I am Aimy, an AI interviewer for Acme\./)).toBeInTheDocument()
    expect(within(dialog).getByText(/Thanks for your time, Alex\./)).toBeInTheDocument()
  })

  it('updates the preview live as the opening script is edited, without calling any API', async () => {
    stubEmptyLists()
    vi.spyOn(agentConfigsApi, 'listAgentConfigs').mockResolvedValue([])
    vi.spyOn(agentConfigsApi, 'getPersonaTemplate').mockResolvedValue(PERSONA)
    const create = vi.spyOn(agentConfigsApi, 'createAgentConfig')

    renderWithProviders(<AgentConfigPage />)
    await userEvent.click(await screen.findByRole('button', { name: /new configuration/i }))
    const dialog = await screen.findByRole('dialog')

    // fireEvent.change, not userEvent.type: curly braces are userEvent's
    // special-key syntax, and this value needs the literal placeholder text.
    const openingField = within(dialog).getByLabelText(/opening script/i)
    fireEvent.change(openingField, { target: { value: 'Welcome {candidate_first_name}!' } })

    expect(within(dialog).getByText(/Welcome Alex!/)).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })
})
