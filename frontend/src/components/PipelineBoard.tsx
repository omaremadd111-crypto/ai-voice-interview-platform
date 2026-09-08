import { useState } from 'react'

import { getPipeline, issuePipelineInvitation } from '@/api/pipeline'
import type { ApplicationState, PipelineRow } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable'
import { EmptyState, ErrorAlert, LoadingBlock } from '@/components/ui/Feedback'
import { UsersIcon } from '@/components/ui/Icons'
import { toMessage, useAsync } from '@/lib/useAsync'

const STAGE_LABEL: Record<ApplicationState, string> = {
  received: 'Received',
  candidate_created: 'Candidate created',
  plan_ready: 'Queued',
  invited: 'Invited',
  abandoned: 'Abandoned',
}

const STAGE_TONE: Record<ApplicationState, 'neutral' | 'info' | 'brand' | 'success' | 'warning'> = {
  received: 'neutral',
  candidate_created: 'info',
  plan_ready: 'brand',
  invited: 'success',
  abandoned: 'warning',
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

/**
 * Applicants who came in through the automated screening pipeline for one
 * position -- see application/application_pipeline_service.py. Read-only
 * except for one action: (re)issuing a candidate's durable interview
 * invitation, for a position where auto_create_invitation is off, or as a
 * one-off resend.
 */
export function PipelineBoard({ positionId }: { positionId: number }) {
  const pipeline = useAsync<PipelineRow[]>(() => getPipeline(positionId), [positionId])
  const [actionError, setActionError] = useState<string | null>(null)
  const [issuedFor, setIssuedFor] = useState<number | null>(null)
  const [issuedUrl, setIssuedUrl] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  async function handleIssue(applicationId: number) {
    setActionError(null)
    setIssuedFor(null)
    setBusyId(applicationId)
    try {
      const issued = await issuePipelineInvitation(positionId, applicationId)
      setIssuedFor(applicationId)
      setIssuedUrl(issued.interview_url)
    } catch (caught) {
      setActionError(toMessage(caught, 'Could not issue an interview invitation.'))
    } finally {
      setBusyId(null)
    }
  }

  if (pipeline.loading) return <LoadingBlock label="Loading applicants…" />
  if (pipeline.error) return <ErrorAlert message={pipeline.error} />

  const rows = pipeline.data ?? []

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<UsersIcon className="h-10 w-10" />}
        title="No applications yet"
        description="Applicants who apply through the public job page will show up here automatically."
      />
    )
  }

  const columns: DataTableColumn<PipelineRow>[] = [
    {
      key: 'applicant',
      header: 'Applicant',
      render: (row) => (
        <div>
          <p className="text-ink-900 font-medium">{row.full_name}</p>
          <p className="text-ink-500 text-xs">{row.email}</p>
        </div>
      ),
    },
    {
      key: 'stage',
      header: 'Stage',
      render: (row) => (
        <div className="space-y-1">
          <Badge tone={STAGE_TONE[row.pipeline_state]}>{STAGE_LABEL[row.pipeline_state]}</Badge>
          {row.cv_parse_error && (
            <p className="text-warning-700 max-w-xs text-xs">CV could not be read</p>
          )}
          {row.last_error && <p className="text-danger-600 max-w-xs text-xs">{row.last_error}</p>}
        </div>
      ),
    },
    {
      key: 'applied',
      header: 'Applied',
      className: 'text-ink-600 whitespace-nowrap',
      render: (row) => formatDate(row.created_at),
    },
    {
      key: 'actions',
      header: '',
      className: 'text-right',
      render: (row) => (
        <div className="flex flex-col items-end gap-1">
          <Button
            size="sm"
            variant="secondary"
            disabled={row.candidate_id === null || busyId === row.application_id}
            loading={busyId === row.application_id}
            onClick={() => void handleIssue(row.application_id)}
          >
            {row.pipeline_state === 'invited' ? 'Resend link' : 'Issue link'}
          </Button>
          {issuedFor === row.application_id && issuedUrl && (
            <span className="text-ink-500 max-w-56 truncate text-xs" title={issuedUrl}>
              {issuedUrl}
            </span>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {actionError && <ErrorAlert message={actionError} />}
      <div className="card overflow-hidden">
        <DataTable caption="Applicants" columns={columns} rows={rows} rowKey={(row) => row.application_id} />
      </div>
    </div>
  )
}
