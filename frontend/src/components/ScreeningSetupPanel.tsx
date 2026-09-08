import { useState } from 'react'

import {
  approveScreeningTemplate,
  getScreeningConfig,
  publishPosition,
  unpublishPosition,
  updateScreeningConfig,
} from '@/api/screeningConfig'
import type { ScreeningConfig } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { TextAreaField, TextField } from '@/components/ui/Field'
import { ErrorAlert, LoadingBlock } from '@/components/ui/Feedback'
import { CheckIcon, DocumentIcon } from '@/components/ui/Icons'
import { toMessage, useAsync } from '@/lib/useAsync'

/** Comma-separated hours -> a sorted list of positive integers, mirroring how
 *  QuestionForm's "expected topics" field parses a free-text list. */
function parseOffsets(raw: string): number[] {
  return raw
    .split(',')
    .map((part) => Number(part.trim()))
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((a, b) => a - b)
}

function CopyLinkField({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard permission denied or unavailable: the field's own text is
      // still selectable, so the recruiter can copy it by hand.
    }
  }

  return (
    <div className="flex items-center gap-2">
      <input
        readOnly
        value={url}
        onFocus={(e) => e.currentTarget.select()}
        className="border-border-strong bg-surface-sunken text-ink-700 w-full truncate rounded-lg border px-3 py-2 text-sm"
      />
      <Button type="button" variant="secondary" size="sm" onClick={() => void copy()}>
        {copied ? <CheckIcon className="h-4 w-4" /> : null}
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

export function ScreeningSetupPanel({
  positionId,
  questionCount,
}: {
  positionId: number
  questionCount: number
}) {
  const config = useAsync<ScreeningConfig>(() => getScreeningConfig(positionId), [positionId])
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [notice, setSettingsNotice] = useState<string | null>(null)

  async function runAction(action: () => Promise<ScreeningConfig>) {
    setActionError(null)
    setBusy(true)
    try {
      const updated = await action()
      config.setData(updated)
    } catch (caught) {
      setActionError(toMessage(caught, 'Could not complete that action.'))
    } finally {
      setBusy(false)
    }
  }

  if (config.loading) return <LoadingBlock label="Loading screening setup…" />
  if (config.error) return <ErrorAlert message={config.error} />
  if (!config.data) return null

  const current = config.data
  const isApproved = current.template_status === 'approved'
  const isPublished = current.accept_public_applications && current.public_url !== null
  const approveDisabledReason =
    questionCount === 0 ? 'Add at least one interview question first.' : null

  return (
    <div className="space-y-6">
      {actionError && <ErrorAlert message={actionError} />}

      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-ink-900 text-sm font-semibold">Screening template</h3>
            <p className="text-ink-500 mt-1 max-w-md text-xs leading-5">
              The job description, question bank, and rubric taken together. A human approves this
              once; every applicant who applies automatically gets exactly this set of questions —
              editing any of them returns the template to draft.
            </p>
          </div>
          <Badge tone={isApproved ? 'success' : 'neutral'}>
            {isApproved ? 'Approved' : 'Draft'}
          </Badge>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={isApproved ? 'secondary' : 'primary'}
            disabled={busy || approveDisabledReason !== null}
            loading={busy}
            onClick={() => void runAction(() => approveScreeningTemplate(positionId))}
          >
            {isApproved ? 'Re-approve template' : 'Approve template'}
          </Button>
          {!isApproved && approveDisabledReason && (
            <span className="text-ink-500 text-xs">{approveDisabledReason}</span>
          )}
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="text-ink-900 text-sm font-semibold">Public application link</h3>
        <p className="text-ink-500 mt-1 max-w-md text-xs leading-5">
          Publish to generate a public job page you can share on LinkedIn or your careers site.
          Applicants cannot yet apply from it in this release — that arrives in a later phase.
        </p>

        {!isApproved && (
          <p className="text-ink-500 mt-3 flex items-center gap-2 text-xs">
            <DocumentIcon className="h-4 w-4" />
            Approve the screening template before publishing.
          </p>
        )}

        {isApproved && !isPublished && (
          <div className="mt-3">
            <Button
              size="sm"
              disabled={busy}
              loading={busy}
              onClick={() => void runAction(() => publishPosition(positionId))}
            >
              Publish
            </Button>
          </div>
        )}

        {isPublished && current.public_url && (
          <div className="mt-3 space-y-3">
            <CopyLinkField url={current.public_url} />
            <div className="flex items-center gap-3">
              <a
                href={current.public_url}
                target="_blank"
                rel="noreferrer"
                className="text-brand-700 text-xs font-medium hover:underline"
              >
                Open job page
              </a>
              <button
                type="button"
                disabled={busy}
                onClick={() => void runAction(() => unpublishPosition(positionId))}
                className="text-ink-500 text-xs font-medium hover:underline disabled:opacity-60"
              >
                Stop accepting applications
              </button>
            </div>
          </div>
        )}

        {isApproved && !current.accept_public_applications && current.public_url && (
          <p className="text-ink-500 mt-3 text-xs">
            Not currently accepting applications.{' '}
            <button
              type="button"
              disabled={busy}
              onClick={() => void runAction(() => publishPosition(positionId))}
              className="text-brand-700 font-medium hover:underline disabled:opacity-60"
            >
              Publish again
            </button>
          </p>
        )}
      </Card>

      <Card className="p-5">
        <h3 className="text-ink-900 text-sm font-semibold">Automation settings</h3>
        <p className="text-ink-500 mt-1 max-w-md text-xs leading-5">
          These take effect as soon as a candidate applies through this position's public page,
          except where noted below.
        </p>
        {notice && <p className="text-success-700 mt-3 text-xs font-medium">{notice}</p>}
        <AutomationSettingsForm
          key={current.position_id}
          config={current}
          onSaved={(updated) => {
            config.setData(updated)
            setSettingsNotice('Settings saved.')
            setTimeout(() => setSettingsNotice(null), 2500)
          }}
        />
      </Card>
    </div>
  )
}

function AutomationSettingsForm({
  config,
  onSaved,
}: {
  config: ScreeningConfig
  onSaved: (updated: ScreeningConfig) => void
}) {
  const [autoParseCv, setAutoParseCv] = useState(config.auto_parse_cv)
  const [autoCreatePlan, setAutoCreatePlan] = useState(config.auto_create_plan)
  const [autoCreateInvitation, setAutoCreateInvitation] = useState(config.auto_create_invitation)
  const [allowImmediateStart, setAllowImmediateStart] = useState(config.allow_immediate_start)
  const [autoEmailInvitation, setAutoEmailInvitation] = useState(config.auto_email_invitation)
  const [allowCvPersonalization, setAllowCvPersonalization] = useState(
    config.allow_cv_personalization,
  )
  const [requirePhone, setRequirePhone] = useState(config.require_phone)
  const [invitationTtlHours, setInvitationTtlHours] = useState(String(config.invitation_ttl_hours))
  const [maxPerDay, setMaxPerDay] = useState(
    config.max_applications_per_day === null ? '' : String(config.max_applications_per_day),
  )
  const [reminderOffsets, setReminderOffsets] = useState(
    config.reminder_offsets_hours.join(', '),
  )
  const [applicationNotice, setApplicationNotice] = useState(config.application_notice ?? '')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit() {
    setError(null)
    setSubmitting(true)
    try {
      const updated = await updateScreeningConfig(config.position_id, {
        auto_parse_cv: autoParseCv,
        auto_create_plan: autoCreatePlan,
        auto_create_invitation: autoCreateInvitation,
        allow_immediate_start: allowImmediateStart,
        auto_email_invitation: autoEmailInvitation,
        allow_cv_personalization: allowCvPersonalization,
        require_phone: requirePhone,
        invitation_ttl_hours: Number(invitationTtlHours) || config.invitation_ttl_hours,
        max_applications_per_day: maxPerDay.trim() === '' ? null : Number(maxPerDay),
        reminder_offsets_hours: parseOffsets(reminderOffsets),
        application_notice: applicationNotice.trim() === '' ? null : applicationNotice,
      })
      onSaved(updated)
    } catch (caught) {
      setError(toMessage(caught, 'Could not save these settings.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-4 space-y-4">
      {error && <ErrorAlert message={error} />}

      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Toggle label="Automatically parse the CV" checked={autoParseCv} onChange={setAutoParseCv} />
        <Toggle
          label="Automatically prepare the interview plan"
          checked={autoCreatePlan}
          onChange={setAutoCreatePlan}
        />
        <Toggle
          label="Automatically create an invitation"
          checked={autoCreateInvitation}
          onChange={setAutoCreateInvitation}
        />
        <Toggle
          label="Allow starting the interview immediately"
          checked={allowImmediateStart}
          onChange={setAllowImmediateStart}
        />
        <Toggle
          label="Email the interview link"
          checked={autoEmailInvitation}
          onChange={setAutoEmailInvitation}
          hint="Sends the candidate their interview link by email right after they apply, so they can start now or come back to it later."
        />
        <Toggle
          label="Personalize follow-up questions from the CV"
          checked={allowCvPersonalization}
          onChange={setAllowCvPersonalization}
          hint="Not yet available."
        />
        <Toggle label="Require a phone number" checked={requirePhone} onChange={setRequirePhone} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Invitation link expires after (hours)"
          type="number"
          min={1}
          value={invitationTtlHours}
          onChange={(e) => setInvitationTtlHours(e.target.value)}
        />
        <TextField
          label="Max applications per day"
          type="number"
          min={1}
          value={maxPerDay}
          onChange={(e) => setMaxPerDay(e.target.value)}
          hint="Leave blank for no limit."
        />
      </div>

      <TextField
        label="Reminder schedule (hours after invitation)"
        value={reminderOffsets}
        onChange={(e) => setReminderOffsets(e.target.value)}
        placeholder="24, 72"
        hint="Comma-separated. Leave blank to send no reminders."
      />

      <TextAreaField
        label="Application page notice"
        value={applicationNotice}
        onChange={(e) => setApplicationNotice(e.target.value)}
        hint="Shown to candidates on the public job page. Optional."
      />

      <div className="flex justify-end pt-1">
        <Button size="sm" disabled={submitting} loading={submitting} onClick={() => void handleSubmit()}>
          Save settings
        </Button>
      </div>
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string
  checked: boolean
  onChange: (value: boolean) => void
  hint?: string
}) {
  return (
    <label className="flex items-start gap-2.5 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="border-ink-300 text-brand-600 mt-0.5 h-4 w-4 rounded"
      />
      <span>
        <span className="text-ink-700 block">{label}</span>
        {hint && <span className="text-ink-400 text-xs">{hint}</span>}
      </span>
    </label>
  )
}
