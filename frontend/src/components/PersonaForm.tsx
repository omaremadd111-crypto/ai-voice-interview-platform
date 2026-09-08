import { useState } from 'react'

import type { ConversationalStyle, VoiceAgentPersona } from '@/api/types'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'

const LANGUAGES = ['English', 'Arabic', 'French', 'German', 'Spanish'] as const

const TONES = [
  'professional and warm',
  'friendly and conversational',
  'concise and businesslike',
  'calm and reassuring',
] as const

const STYLE_FIELDS: Array<{ key: keyof ConversationalStyle; label: string; hint: string }> = [
  {
    key: 'use_candidate_name',
    label: 'Use the candidate’s name',
    hint: 'Naturally, at the opening and when changing topic — not before every question.',
  },
  {
    key: 'brief_acknowledgements',
    label: 'Brief acknowledgements',
    hint: 'Short confirmations like “got it” so the candidate knows they were heard.',
  },
  {
    key: 'allow_question_rephrasing',
    label: 'Rephrase when asked',
    hint: 'Restate a question in different words if the candidate does not follow it.',
  },
  {
    key: 'natural_pauses',
    label: 'Natural pauses',
    hint: 'Leave room for the candidate to think instead of filling every silence.',
  },
  {
    key: 'allow_interruptions',
    label: 'Allow interruptions (barge-in)',
    hint: 'Stop speaking when the candidate starts, rather than talking over them.',
  },
]

/**
 * Structured editor for the voice agent's persona.
 *
 * These settings shape how the interview *sounds*. They never reach the
 * evaluator, the score, or the screening outcome — the backend enforces that,
 * and also rejects an opening that fails to disclose the agent is an AI.
 */
export function PersonaForm({
  persona,
  onChange,
  disabled = false,
}: {
  persona: VoiceAgentPersona
  onChange: (persona: VoiceAgentPersona) => void
  disabled?: boolean
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  function set<K extends keyof VoiceAgentPersona>(key: K, value: VoiceAgentPersona[K]) {
    onChange({ ...persona, [key]: value })
  }

  function setStyle(key: keyof ConversationalStyle, value: boolean) {
    onChange({ ...persona, conversational_style: { ...persona.conversational_style, [key]: value } })
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Agent name"
          required
          disabled={disabled}
          value={persona.agent_name}
          onChange={(e) => set('agent_name', e.target.value)}
          placeholder="Aimy"
        />
        <TextField
          label="Company name"
          required
          disabled={disabled}
          value={persona.company_name}
          onChange={(e) => set('company_name', e.target.value)}
          placeholder="FlairsTech"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label="AI role / title"
          disabled={disabled}
          value={persona.ai_role_title}
          onChange={(e) => set('ai_role_title', e.target.value)}
          placeholder="AI screening assistant"
        />
        <SelectField
          label="Language"
          disabled={disabled}
          value={persona.language}
          onChange={(e) => set('language', e.target.value)}
        >
          {LANGUAGES.map((language) => (
            <option key={language} value={language}>
              {language}
            </option>
          ))}
          {!LANGUAGES.includes(persona.language as (typeof LANGUAGES)[number]) && (
            <option value={persona.language}>{persona.language}</option>
          )}
        </SelectField>
        <SelectField
          label="Tone"
          disabled={disabled}
          value={persona.tone}
          onChange={(e) => set('tone', e.target.value)}
        >
          {TONES.map((tone) => (
            <option key={tone} value={tone}>
              {tone}
            </option>
          ))}
          {!TONES.includes(persona.tone as (typeof TONES)[number]) && (
            <option value={persona.tone}>{persona.tone}</option>
          )}
        </SelectField>
      </div>

      <TextAreaField
        label="Opening script"
        required
        disabled={disabled}
        value={persona.opening_script}
        onChange={(e) => set('opening_script', e.target.value)}
        className="min-h-24"
        hint="Must state that the interviewer is an AI. Use {candidate_first_name} to greet the candidate by name."
      />
      <TextAreaField
        label="Closing script"
        required
        disabled={disabled}
        value={persona.closing_script}
        onChange={(e) => set('closing_script', e.target.value)}
        className="min-h-20"
      />

      <div>
        <button
          type="button"
          onClick={() => setShowAdvanced((open) => !open)}
          className="text-brand-700 text-sm font-medium hover:underline"
        >
          {showAdvanced ? 'Hide' : 'Show'} conversation handling
        </button>
      </div>

      {showAdvanced && (
        <div className="border-ink-200 space-y-4 border-t pt-4">
          <TextAreaField
            label="Off-topic redirection"
            disabled={disabled}
            value={persona.off_topic_redirection}
            onChange={(e) => set('off_topic_redirection', e.target.value)}
            hint="How the agent steers back on track without dismissing the candidate."
          />
          <TextAreaField
            label="Follow-up style"
            disabled={disabled}
            value={persona.follow_up_style}
            onChange={(e) => set('follow_up_style', e.target.value)}
            hint="How probing follow-ups should be. Limits are still enforced by the interview engine."
          />
          <TextAreaField
            label="Handling candidate questions"
            disabled={disabled}
            value={persona.candidate_question_handling}
            onChange={(e) => set('candidate_question_handling', e.target.value)}
            hint="What the agent may answer, and what it should defer to a human recruiter."
          />

          <fieldset className="space-y-2">
            <legend className="text-ink-700 mb-1 text-sm font-medium">Conversational style</legend>
            {STYLE_FIELDS.map(({ key, label, hint }) => (
              <label key={key} className="flex items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  disabled={disabled}
                  checked={persona.conversational_style[key]}
                  onChange={(e) => setStyle(key, e.target.checked)}
                  className="border-ink-300 text-brand-600 mt-0.5 h-4 w-4 shrink-0 rounded"
                />
                <span>
                  <span className="text-ink-800">{label}</span>
                  <span className="text-ink-500 block text-xs">{hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
        </div>
      )}
    </div>
  )
}
