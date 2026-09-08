import { useState, type FormEvent } from 'react'

import {
  createAgentConfig,
  getPersonaTemplate,
  listAgentConfigs,
  updateAgentConfig,
} from '@/api/agentConfigs'
import { listPositions } from '@/api/positions'
import type { AgentConfig, Position, VoiceAgentPersona } from '@/api/types'
import { PersonaForm } from '@/components/PersonaForm'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { SelectField, TextField } from '@/components/ui/Field'
import { EmptyState, ErrorAlert, LoadingBlock, Spinner } from '@/components/ui/Feedback'
import { PlusIcon, RobotIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { toMessage, useAsync } from '@/lib/useAsync'

/** A config is a voice persona when it carries the identity fields the backend
 *  validates against; older/unrelated blobs are shown read-only as JSON. */
function asPersona(config: Record<string, unknown>): VoiceAgentPersona | null {
  return typeof config.agent_name === 'string' && typeof config.company_name === 'string'
    ? (config as unknown as VoiceAgentPersona)
    : null
}

/** A stand-in first name so a script's {candidate_first_name} placeholder
 *  reads naturally in the preview instead of showing the literal token. */
const SAMPLE_CANDIDATE_NAME = 'Alex'

function withSampleName(script: string): string {
  return script.replaceAll('{candidate_first_name}', SAMPLE_CANDIDATE_NAME)
}

/** Client-side only: reads the persona state the form already holds and
 *  substitutes a sample name into the placeholder so HR can see roughly how
 *  the opening/closing will sound. Nothing here is sent anywhere -- it exists
 *  purely so a script can be checked for tone and length before saving. */
function PersonaPreview({ persona }: { persona: VoiceAgentPersona }) {
  return (
    <div className="border-border-subtle bg-surface-sunken space-y-3 rounded-lg border p-4">
      <p className="text-ink-500 text-xs">
        {persona.agent_name || 'The agent'} · {persona.ai_role_title || 'AI screening assistant'}{' '}
        at {persona.company_name || 'your company'}
      </p>
      <div>
        <p className="text-ink-400 text-[11px] font-semibold tracking-wide uppercase">Opening</p>
        <p className="text-ink-700 mt-1 text-sm italic">
          “{persona.opening_script ? withSampleName(persona.opening_script) : '—'}”
        </p>
      </div>
      <div>
        <p className="text-ink-400 text-[11px] font-semibold tracking-wide uppercase">Closing</p>
        <p className="text-ink-700 mt-1 text-sm italic">
          “{persona.closing_script ? withSampleName(persona.closing_script) : '—'}”
        </p>
      </div>
    </div>
  )
}

function ConfigForm({
  initialName,
  initialPositionId,
  initialPersona,
  initialActive,
  lockPosition,
  positions,
  submitLabel,
  onSubmit,
}: {
  initialName: string
  initialPositionId: string
  initialPersona: VoiceAgentPersona
  initialActive: boolean
  lockPosition: boolean
  positions: Position[]
  submitLabel: string
  onSubmit: (values: {
    name: string
    positionId: string
    persona: VoiceAgentPersona
    isActive: boolean
  }) => Promise<void>
}) {
  const [name, setName] = useState(initialName)
  const [positionId, setPositionId] = useState(initialPositionId)
  const [persona, setPersona] = useState(initialPersona)
  const [isActive, setIsActive] = useState(initialActive)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit({ name, positionId, persona, isActive })
    } catch (caught) {
      setError(toMessage(caught, 'Could not save the configuration.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      {error && <ErrorAlert message={error} />}

      <div>
        <h4 className="text-ink-500 text-xs font-semibold tracking-wide uppercase">
          Configuration
        </h4>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <TextField
            label="Configuration name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Aimy — FlairsTech screening"
          />
          {!lockPosition && (
            <SelectField
              label="Applies to"
              value={positionId}
              onChange={(e) => setPositionId(e.target.value)}
              hint="Leave as “All positions” for a workspace-wide default."
            >
              <option value="">All positions</option>
              {positions.map((position) => (
                <option key={position.id} value={position.id}>
                  {position.title} — {position.company_name}
                </option>
              ))}
            </SelectField>
          )}
        </div>
        <label className="mt-4 flex items-center gap-2.5 text-sm">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            className="border-ink-300 text-brand-600 h-4 w-4 rounded"
          />
          <span className="text-ink-700">Active</span>
        </label>
      </div>

      <div className="border-border-subtle border-t pt-5">
        <h4 className="text-ink-500 text-xs font-semibold tracking-wide uppercase">Persona</h4>
        <div className="mt-3">
          <PersonaForm persona={persona} onChange={setPersona} disabled={submitting} />
        </div>
      </div>

      <div className="border-border-subtle border-t pt-5">
        <div className="flex items-center justify-between gap-3">
          <h4 className="text-ink-500 text-xs font-semibold tracking-wide uppercase">Preview</h4>
          <span className="text-ink-400 text-xs">Sample only — not sent anywhere</span>
        </div>
        <div className="mt-3">
          <PersonaPreview persona={persona} />
        </div>
      </div>

      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}

export function AgentConfigPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [template, setTemplate] = useState<VoiceAgentPersona | null>(null)
  const [editing, setEditing] = useState<AgentConfig | null>(null)
  const [error, setError] = useState<string | null>(null)

  const configs = useAsync<AgentConfig[]>(() => listAgentConfigs(), [])
  const positions = useAsync<Position[]>(() => listPositions(), [])

  const positionList = positions.data ?? []
  const configList = configs.data ?? []

  function positionLabel(positionId: number | null): string {
    if (positionId === null) return 'All positions'
    return positionList.find((position) => position.id === positionId)?.title ?? `Position #${positionId}`
  }

  async function openCreate() {
    setError(null)
    setCreating(true)
    try {
      // Seeded from the backend so the starting scripts already satisfy the
      // AI-disclosure rule rather than failing on first save.
      setTemplate(await getPersonaTemplate('Aimy', positionList[0]?.company_name ?? 'Your Company'))
      setCreateOpen(true)
    } catch (caught) {
      setError(toMessage(caught, 'Could not load the persona template.'))
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Agent Configuration"
        description="How the AI interviewer introduces itself and handles the conversation."
        actions={
          <Button onClick={openCreate} disabled={creating}>
            {creating ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : <PlusIcon className="h-4 w-4" />}
            New configuration
          </Button>
        }
      />

      <div className="border-brand-200 bg-brand-50 text-brand-900 mb-6 rounded-lg border px-4 py-3 text-sm">
        These settings shape tone and delivery only. They never influence candidate scores,
        evidence requirements, or screening outcomes. The agent always discloses that it is an
        AI, and never analyses emotion, accent, personality, or biometrics.
      </div>

      {error && <ErrorAlert message={error} className="mb-4" />}
      {configs.loading && <LoadingBlock />}
      {configs.error && <ErrorAlert message={configs.error} />}

      {!configs.loading && !configs.error && configList.length === 0 && (
        <div className="card">
          <EmptyState
            icon={<RobotIcon className="h-10 w-10" />}
            title="No agent configurations"
            description="Create one to set the agent's name, opening script, and how it handles the conversation."
            action={
              <Button onClick={openCreate} disabled={creating}>
                <PlusIcon className="h-4 w-4" />
                New configuration
              </Button>
            }
          />
        </div>
      )}

      {configList.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {configList.map((config) => {
            const persona = asPersona(config.config)
            return (
              <article key={config.id} className="card flex flex-col p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="bg-brand-50 text-brand-600 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <RobotIcon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="text-ink-900 truncate text-sm font-semibold">{config.name}</h3>
                      <p className="text-ink-500 mt-0.5 text-xs">{positionLabel(config.position_id)}</p>
                    </div>
                  </div>
                  <Badge tone={config.is_active ? 'success' : 'neutral'}>
                    {config.is_active ? 'Active' : 'Inactive'}
                  </Badge>
                </div>

                {persona ? (
                  <dl className="mt-4 flex-1 space-y-2 text-sm">
                    <div className="flex justify-between gap-3">
                      <dt className="text-ink-500">Agent</dt>
                      <dd className="text-ink-800 truncate">
                        {persona.agent_name} · {persona.ai_role_title}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-ink-500">Company</dt>
                      <dd className="text-ink-800 truncate">{persona.company_name}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-ink-500">Language · tone</dt>
                      <dd className="text-ink-800 truncate">
                        {persona.language} · {persona.tone}
                      </dd>
                    </div>
                    <div className="border-ink-200 mt-2 border-t pt-2">
                      <dt className="text-ink-500 mb-1 text-xs">Opening</dt>
                      <dd className="text-ink-700 line-clamp-3 text-xs italic">
                        “{persona.opening_script}”
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <pre className="bg-ink-50 text-ink-700 mt-4 max-h-40 flex-1 overflow-auto rounded-lg p-3 font-mono text-xs">
                    {JSON.stringify(config.config, null, 2)}
                  </pre>
                )}

                <div className="mt-4 flex justify-end">
                  <Button size="sm" variant="secondary" onClick={() => setEditing(config)}>
                    Edit
                  </Button>
                </div>
              </article>
            )
          })}
        </div>
      )}

      <Modal
        open={createOpen && template !== null}
        title="New agent configuration"
        description="Set how the AI interviewer introduces itself and paces the conversation."
        onClose={() => setCreateOpen(false)}
      >
        {template && (
          <ConfigForm
            initialName=""
            initialPositionId=""
            initialPersona={template}
            initialActive
            lockPosition={false}
            positions={positionList}
            submitLabel="Create configuration"
            onSubmit={async ({ name, positionId, persona, isActive }) => {
              await createAgentConfig({
                name,
                position_id: positionId === '' ? null : Number(positionId),
                config: persona as unknown as Record<string, unknown>,
                is_active: isActive,
              })
              setCreateOpen(false)
              configs.reload()
            }}
          />
        )}
      </Modal>

      <Modal
        open={editing !== null}
        title="Edit agent configuration"
        onClose={() => setEditing(null)}
      >
        {editing &&
          (asPersona(editing.config) ? (
            <ConfigForm
              initialName={editing.name}
              initialPositionId={editing.position_id === null ? '' : String(editing.position_id)}
              initialPersona={asPersona(editing.config)!}
              initialActive={editing.is_active}
              // Which position a config belongs to is fixed at creation -- the
              // backend's update schema has no position_id field.
              lockPosition
              positions={positionList}
              submitLabel="Save configuration"
              onSubmit={async ({ name, persona, isActive }) => {
                await updateAgentConfig(editing.id, {
                  name,
                  config: persona as unknown as Record<string, unknown>,
                  is_active: isActive,
                })
                setEditing(null)
                configs.reload()
              }}
            />
          ) : (
            <p className="text-ink-600 text-sm">
              This configuration predates the voice-agent persona format and has no editable
              fields. Create a new configuration to use the persona settings.
            </p>
          ))}
      </Modal>
    </>
  )
}
