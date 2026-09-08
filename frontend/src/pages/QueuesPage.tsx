import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { listPositions } from '@/api/positions'
import { createQueue, listAllQueues, type QueueWithPosition } from '@/api/queues'
import type { Position } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { QueueStatusBadge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable'
import { SelectField, TextField } from '@/components/ui/Field'
import {
  EmptyState,
  ErrorAlert,
  ErrorState,
  Skeleton,
  Spinner,
  SuccessAlert,
} from '@/components/ui/Feedback'
import { PlusIcon, QueueIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { toMessage, useAsync } from '@/lib/useAsync'

function NewQueueForm({
  positions,
  onCreated,
}: {
  positions: Position[]
  onCreated: (name: string) => void
}) {
  const [positionId, setPositionId] = useState(
    positions.length > 0 ? String(positions[0]!.id) : '',
  )
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (positionId === '') {
      setError('Choose the position this queue screens for.')
      return
    }
    setSubmitting(true)
    try {
      const created = await createQueue(Number(positionId), name)
      onCreated(created.name)
    } catch (caught) {
      setError(toMessage(caught, 'Could not create the queue.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <SelectField
        label="Position"
        required
        value={positionId}
        onChange={(e) => setPositionId(e.target.value)}
        hint="A queue screens candidates for one position."
      >
        <option value="">Select a position…</option>
        {positions.map((position) => (
          <option key={position.id} value={position.id}>
            {position.title} — {position.company_name}
          </option>
        ))}
      </SelectField>
      <TextField
        label="Queue name"
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Junior AI Engineer — round 1"
      />
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          Create queue
        </Button>
      </div>
    </form>
  )
}

/**
 * The calling queues overview.
 *
 * Nothing on this page runs a screening. Queues are worked by a background worker
 * process, so what a recruiter controls here is only whether that worker may pick
 * a queue up — the calls continue whether or not this page is open.
 */
export function QueuesPage() {
  const [modalOpen, setModalOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const positions = useAsync<Position[]>(() => listPositions(), [])
  const queues = useAsync<QueueWithPosition[]>(() => listAllQueues(), [])

  const loading = positions.loading || queues.loading
  const error = positions.error ?? queues.error
  const positionList = positions.data ?? []
  const rows = queues.data ?? []

  function reloadAll() {
    positions.reload()
    queues.reload()
  }

  const columns: DataTableColumn<QueueWithPosition>[] = [
    {
      key: 'queue',
      header: 'Queue',
      render: ({ queue }) => (
        <Link
          to={`/queues/${queue.id}`}
          className="text-ink-900 hover:text-brand-700 font-medium"
        >
          {queue.name}
        </Link>
      ),
    },
    {
      key: 'position',
      header: 'Position',
      className: 'text-ink-600',
      render: ({ position }) => (
        <Link to={`/positions/${position.id}`} className="hover:text-brand-700">
          {position.title}
        </Link>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: ({ queue }) => <QueueStatusBadge status={queue.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title="Queues"
        description="Batch screening runs worked by the background worker, independently of this browser."
        actions={
          <Button
            onClick={() => {
              setNotice(null)
              setModalOpen(true)
            }}
            disabled={positionList.length === 0}
          >
            <PlusIcon className="h-4 w-4" />
            New queue
          </Button>
        }
      />

      {notice !== null && <SuccessAlert className="mb-4" message={notice} />}

      {loading && (
        <div className="card space-y-3 p-5">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}
      {error && <ErrorState message={error} onRetry={reloadAll} />}

      {!loading && !error && positionList.length === 0 && (
        <div className="card">
          <EmptyState
            icon={<QueueIcon className="h-10 w-10" />}
            title="Create a position first"
            description="A queue screens candidates for one position, so you need a position before you can build a queue."
            action={
              <Link to="/positions">
                <Button>Go to positions</Button>
              </Link>
            }
          />
        </div>
      )}

      {!loading && !error && positionList.length > 0 && rows.length === 0 && (
        <div className="card">
          <EmptyState
            icon={<QueueIcon className="h-10 w-10" />}
            title="No queues yet"
            description="Create a queue, add candidates with an approved interview plan, then start it."
            action={
              <Button onClick={() => setModalOpen(true)}>
                <PlusIcon className="h-4 w-4" />
                New queue
              </Button>
            }
          />
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <>
          <div className="mb-3 flex justify-end">
            <span className="text-ink-400 text-sm">
              {rows.length} {rows.length === 1 ? 'queue' : 'queues'}
            </span>
          </div>
          <div className="card overflow-hidden">
            <DataTable
              caption="Queues"
              columns={columns}
              rows={rows}
              rowKey={({ queue }) => queue.id}
            />
          </div>
        </>
      )}

      <Modal
        open={modalOpen}
        title="New queue"
        description="Candidates are added after the queue exists."
        onClose={() => setModalOpen(false)}
      >
        <NewQueueForm
          positions={positionList}
          onCreated={(name) => {
            setModalOpen(false)
            setNotice(`“${name}” was created. Add candidates, then start the queue.`)
            queues.reload()
          }}
        />
      </Modal>
    </>
  )
}
