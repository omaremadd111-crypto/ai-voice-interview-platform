import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { createPosition, listPositions } from '@/api/positions'
import type { Position, PositionStatus } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { PositionStatusBadge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'
import { EmptyState, ErrorAlert, ErrorState, Skeleton, Spinner } from '@/components/ui/Feedback'
import { BriefcaseIcon, PlusIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { useAsync, toMessage } from '@/lib/useAsync'

const EXPERIENCE_LEVELS = [
  'Intern',
  'Junior',
  'Mid-Level',
  'Senior',
  'Lead',
  'Principal',
  'Executive',
] as const

const STATUSES: PositionStatus[] = ['draft', 'active', 'closed']

function CreatePositionForm({ onCreated }: { onCreated: () => void }) {
  const [companyName, setCompanyName] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [experienceLevel, setExperienceLevel] = useState('')
  const [threshold, setThreshold] = useState('')
  const [status, setStatus] = useState<PositionStatus>('draft')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await createPosition({
        company_name: companyName,
        title,
        description: description.trim() === '' ? null : description,
        experience_level: experienceLevel === '' ? null : experienceLevel,
        pass_score_threshold: threshold === '' ? null : Number(threshold),
        status,
      })
      onCreated()
    } catch (caught) {
      setError(toMessage(caught, 'Could not create the position.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Company"
          required
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="Acme Inc."
        />
        <TextField
          label="Job title"
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Junior AI Engineer"
        />
      </div>
      <TextAreaField
        label="Job description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Responsibilities, required skills, technologies…"
        hint="Optional. Used by the AI agent to understand the role."
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <SelectField
          label="Experience level"
          value={experienceLevel}
          onChange={(e) => setExperienceLevel(e.target.value)}
        >
          <option value="">Not specified</option>
          {EXPERIENCE_LEVELS.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Pass threshold"
          type="number"
          min={0}
          max={100}
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          placeholder="60"
          hint="0–100. Optional."
        />
        <SelectField
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as PositionStatus)}
        >
          {STATUSES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </SelectField>
      </div>
      <div className="flex justify-end gap-2 pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          Create position
        </Button>
      </div>
    </form>
  )
}

export function PositionsPage() {
  const [modalOpen, setModalOpen] = useState(false)
  const [statusFilter, setStatusFilter] = useState<PositionStatus | ''>('')
  const { data, loading, error, reload } = useAsync(
    () => listPositions(statusFilter === '' ? undefined : statusFilter),
    [statusFilter],
  )

  const columns: DataTableColumn<Position>[] = [
    {
      key: 'title',
      header: 'Title',
      render: (position) => (
        <Link
          to={`/positions/${position.id}`}
          className="text-ink-900 hover:text-brand-700 font-medium"
        >
          {position.title}
        </Link>
      ),
    },
    {
      key: 'company',
      header: 'Company',
      className: 'text-ink-600',
      render: (position) => position.company_name,
    },
    {
      key: 'level',
      header: 'Level',
      className: 'text-ink-600',
      render: (position) => position.experience_level ?? <span className="text-ink-400">—</span>,
    },
    {
      key: 'threshold',
      header: 'Threshold',
      className: 'text-ink-600 tabular-nums',
      render: (position) =>
        position.pass_score_threshold ?? <span className="text-ink-400">—</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (position) => <PositionStatusBadge status={position.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title="Positions"
        description="Roles you are screening for. Each position owns its own interview questions."
        actions={
          <Button onClick={() => setModalOpen(true)}>
            <PlusIcon className="h-4 w-4" />
            New position
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="status-filter" className="text-ink-600 text-sm font-medium">
            Status
          </label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as PositionStatus | '')}
            className="border-border-default text-ink-800 bg-surface-raised rounded-lg border px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {STATUSES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        {!loading && !error && data && (
          <span className="text-ink-400 text-sm">
            {data.length} {data.length === 1 ? 'position' : 'positions'}
          </span>
        )}
      </div>

      {loading && (
        <div className="card space-y-3 p-5">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}
      {error && <ErrorState message={error} onRetry={reload} />}

      {!loading && !error && data && data.length === 0 && (
        <div className="card">
          <EmptyState
            icon={<BriefcaseIcon className="h-10 w-10" />}
            title={statusFilter === '' ? 'No positions yet' : `No ${statusFilter} positions`}
            description="Create a position to define the role and the questions the AI agent will ask."
            action={
              <Button onClick={() => setModalOpen(true)}>
                <PlusIcon className="h-4 w-4" />
                New position
              </Button>
            }
          />
        </div>
      )}

      {!loading && !error && data && data.length > 0 && (
        <div className="card overflow-hidden">
          <DataTable caption="Positions" columns={columns} rows={data} rowKey={(p) => p.id} />
        </div>
      )}

      <Modal
        open={modalOpen}
        title="New position"
        description="Define the role. You can add interview questions next."
        onClose={() => setModalOpen(false)}
      >
        <CreatePositionForm
          onCreated={() => {
            setModalOpen(false)
            reload()
          }}
        />
      </Modal>
    </>
  )
}
