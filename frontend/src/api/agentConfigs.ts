import { request } from './client'
import type {
  AgentConfig,
  AgentConfigCreate,
  AgentConfigUpdate,
  VoiceAgentPersona,
} from './types'

/** A valid starting persona from the backend, so the form is seeded with an
 *  example that already satisfies the AI-disclosure rule. */
export function getPersonaTemplate(
  agentName: string,
  companyName: string,
): Promise<VoiceAgentPersona> {
  return request<VoiceAgentPersona>('/api/v1/agent-configs/persona-template', {
    query: { agent_name: agentName, company_name: companyName },
  })
}

export function listAgentConfigs(): Promise<AgentConfig[]> {
  return request<AgentConfig[]>('/api/v1/agent-configs')
}

export function getAgentConfig(id: number): Promise<AgentConfig> {
  return request<AgentConfig>(`/api/v1/agent-configs/${id}`)
}

export function createAgentConfig(payload: AgentConfigCreate): Promise<AgentConfig> {
  return request<AgentConfig>('/api/v1/agent-configs', { method: 'POST', json: payload })
}

export function updateAgentConfig(id: number, payload: AgentConfigUpdate): Promise<AgentConfig> {
  return request<AgentConfig>(`/api/v1/agent-configs/${id}`, { method: 'PATCH', json: payload })
}
