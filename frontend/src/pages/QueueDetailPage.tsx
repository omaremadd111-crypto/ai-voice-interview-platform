import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  addQueueItem,
  cancelQueueItem,
  deleteQueue,
  loadQueueDetail,
  pauseQueue,
  removeQueueItem,
  resumeQueue,
  retryQueueItem,
  startQueue,
  type QueueDetail,
} from '@/api/queues'
import type { Candidate, QueueItem, QueueItemStatus, ScreeningResult } from '@/api/types'
import { createVoiceInvitation } from '@/api/voice'
import { PageHeader } from '@/components/layout/PageHeader'
import { Avatar } from '@/components/ui/Avatar'
import {
  QueueItemStatusBadge,
  QueueStatusBadge,
  ScreeningOutcomeBadge,
} from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { SelectField } from '@/components/ui/Field'
import {
  EmptyState,
  ErrorAlert,
  ErrorState,
  Skeleton,
  Spinner,
} from '@/components/ui/Feedback'
import { PlusIcon, QueueIcon, TrashIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { cn } from '@/lib/cn'
import { QUEUE_ITEM_STATUS_LABEL } from '@/lib/labels'
import { toMessage, useAsync } from '@/lib/useAsync'

/** States in which the worker currently holds the item; a recruiter must not
 *  pull it out from under a call that is already under way. */
const IN_FLIGHT: QueueItemStatus[] = ['claimed', 'in_progress']

const SUMMARY_ORDER: QueueItemStatus[] = [
  'pending',
  'in_progress',
  'completed',
  'no_answer',
  'failed',
  'cancelled',
]

// Every status, for the proportional bar -- unlike SUMMARY_ORDER (the six
// tiles below it), this must sum to progress.total, so the fleeting
// "claimed" state is included even though it gets no tile of its own.
// "awaiting_candidate" only ever appears in an auto-pipeline queue (hidden
// from the main Queues list, but still reachable by its own URL) -- included
// here so that page never breaks on one.
const BAR_ORDER: QueueItemStatus[] = [
  'awaiting_candidate',
  'pending',
  'claimed',
  'in_progress',
  'completed',
  'no_answer',
  'failed',
  'cancelled',
]

const BAR_TONE: Record<QueueItemStatus, string> = {
  awaiting_candidate: 'bg-ink-200',
  pending: 'bg-ink-300',
  claimed: 'bg-info-300',
  in_progress: 'bg-brand-400',
  completed: 'bg-success-400',
  no_answer: 'bg-warning-300',
  failed: 'bg-warning-500',
  cancelled: 'bg-ink-200',
}

function formatTime(value: string | null): string {
  if (value === null) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString()
}

function ProgressSummary({ detail }: { detail: QueueDetail }) {
  const { progress } = detail
  const pct = progress.total > 0 ? Math.round((progress.finished / progress.total) * 100) : 0

  return (
    <div
      className={cn(
        'card mb-6 border-l-4 p-5',
        progress.status === 'running'
          ? 'border-l-success-400'
          : progress.status === 'paused'
            ? 'border-l-warning-400'
            : 'border-l-border-strong',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <QueueStatusBadge status={progress.status} />
            <span className="text-ink-900 text-sm font-semibold">
              {progress.finished} of {progress.total} finished
            </span>
          </div>
          <p className="text-ink-500 mt-1 text-sm">
            {progress.in_flight > 0
              ? `${progress.in_flight} screening in progress right now.`
              : progress.status === 'running'
                ? 'Running — waiting for the next candidate to be claimed.'
                : 'No screening is running right now.'}
          </p>
        </div>
        {progress.total > 0 && (
          <span className="text-ink-400 shrink-0 text-sm tabular-nums">{pct}% complete</span>
        )}
      </div>

      {progress.total > 0 && (
        <div
          className="border-border-subtle bg-surface-sunken mt-4 flex h-2 overflow-hidden rounded-full border"
          role="img"
          aria-label={`${pct}% of this queue's items are finished`}
        >
          {BAR_ORDER.filter((status) => (progress.counts[status] ?? 0) > 0).map((status) => (
            <div
              key={status}
              className={cn(BAR_TONE[status], 'h-full first:rounded-l-full last:rounded-r-full')}
              style={{ width: `${((progress.counts[status] ?? 0) / progress.total) * 100}%` }}
              title={`${QUEUE_ITEM_STATUS_LABEL[status]}: ${progress.counts[status] ?? 0}`}
            />
          ))}
        </div>
      )}

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {SUMMARY_ORDER.map((status) => (
          <div key={status} className="bg-surface-sunken rounded-lg px-3 py-2">
            <dt className="text-ink-500 text-[11px] font-medium tracking-wide uppercase">
              {QUEUE_ITEM_STATUS_LABEL[status]}
            </dt>
            <dd className="text-ink-900 mt-0.5 text-lg font-semibold tabular-nums">
              {progress.counts[status] ?? 0}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function AddCandidateForm({
  available,
  onAdd,
}: {
  available: Candidate[]
  onAdd: (candidateId: number) => Promise<void>
}) {
  const [candidateId, setCandidateId] = useState(
    available.length > 0 ? String(available[0]!.id) : '',
  )
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (available.length === 0) {
    return (
      <p className="text-ink-500 text-sm">
        Everyone on this position is already in the queue.
      </p>
    )
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    if (candidateId === '') {
      setError('Choose a candidate to add.')
      return
    }
    setSubmitting(true)
    try {
      await onAdd(Number(candidateId))
    } catch (caught) {
      setError(toMessage(caught, 'Could not add the candidate to the queue.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <SelectField
        label="Candidate"
        required
        value={candidateId}
        onChange={(e) => setCandidateId(e.target.value)}
        hint="A candidate can only be queued once their interview plan has been approved."
      >
        {available.map((candidate) => (
          <option key={candidate.id} value={candidate.id}>
            {candidate.full_name}
          </option>
        ))}
      </SelectField>
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          Add to queue
        </Button>
      </div>
    </form>
  )
}

function ItemRow({
  item,
  candidate,
  result,
  onAction,
}: {
  item: QueueItem
  candidate: Candidate | undefined
  result: ScreeningResult | null | undefined
  onAction: (action: () => Promise<unknown>) => void
}) {
  const inFlight = IN_FLIGHT.includes(item.status)
  const [invitation, setInvitation] = useState<string | null>(null)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [creatingInvite, setCreatingInvite] = useState(false)

  async function prepareInvitation() {
    setCreatingInvite(true)
    setInviteError(null)
    try {
      const created = await createVoiceInvitation(item.queue_id, item.id)
      setInvitation(created.invite_url)
    } catch (caught) {
      setInviteError(toMessage(caught, 'Could not create the candidate interview link.'))
    } finally {
      setCreatingInvite(false)
    }
  }
  return (
    <tr className="hover:bg-ink-50 align-top transition-colors">
      <td className="px-5 py-3.5">
        {candidate === undefined ? (
          <span className="text-ink-400">Candidate {item.candidate_id}</span>
        ) : (
          <Link
            to={`/candidates/${candidate.id}`}
            className="hover:text-brand-700 flex items-center gap-2.5"
          >
            <Avatar name={candidate.full_name} size="sm" />
            <span className="text-ink-900 font-medium">{candidate.full_name}</span>
          </Link>
        )}
      </td>
      <td className="px-5 py-3.5">
        <QueueItemStatusBadge status={item.status} />
        {item.last_error !== null && (
          <p className="text-ink-500 mt-1 max-w-xs text-xs">{item.last_error}</p>
        )}
      </td>
      <td className="px-5 py-3.5 whitespace-nowrap">
        <span
          className={cn(
            'font-medium tabular-nums',
            item.attempts >= item.max_attempts ? 'text-warning-700' : 'text-ink-600',
          )}
        >
          {item.attempts} / {item.max_attempts}
        </span>
      </td>
      <td className="px-5 py-3.5 whitespace-nowrap">
        {result === undefined || result === null ? (
          <span className="text-ink-400">—</span>
        ) : (
          <div className="space-y-1">
            <ScreeningOutcomeBadge outcome={result.screening_outcome} />
            <div className="text-ink-600 text-xs">
              {result.overall_score === null ? 'No reliable score' : `${result.overall_score}/100`}
              {' · '}{Math.round(result.evidence_coverage * 100)}% evidence
            </div>
            {candidate !== undefined && (
              <Link
                to={`/candidates/${candidate.id}#screening-report`}
                className="text-brand-700 block text-xs font-medium hover:underline"
              >
                View full report
              </Link>
            )}
          </div>
        )}
      </td>
      <td className="text-ink-600 px-5 py-3.5 text-xs whitespace-nowrap">
        {item.status === 'pending' && item.next_attempt_at !== null
          ? `Next attempt ${formatTime(item.next_attempt_at)}`
          : inFlight
            ? `Worker ${item.claimed_by ?? 'unknown'}`
            : '—'}
        {invitation !== null && (
          <a
            href={invitation}
            target="_blank"
            rel="noreferrer"
            className="text-brand-700 mt-1 block font-medium hover:underline"
          >
            Open candidate link
          </a>
        )}
        {inviteError !== null && <p className="text-danger-700 mt-1 max-w-xs">{inviteError}</p>}
      </td>
      <td className="px-5 py-3.5">
        <div className="flex flex-wrap justify-end gap-2">
          {item.status === 'in_progress' && item.interview_session_id !== null && (
            <Button
              size="sm"
              variant="secondary"
              disabled={creatingInvite}
              onClick={prepareInvitation}
            >
              {creatingInvite ? 'Preparing…' : 'Candidate link'}
            </Button>
          )}
          {!inFlight && item.status !== 'cancelled' && item.status !== 'completed' && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onAction(() => cancelQueueItem(item.queue_id, item.id))}
            >
              Cancel
            </Button>
          )}
          {(item.status === 'failed' ||
            item.status === 'no_answer' ||
            item.status === 'cancelled') && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onAction(() => retryQueueItem(item.queue_id, item.id))}
            >
              Try again
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Remove ${candidate?.full_name ?? `candidate ${item.candidate_id}`} from the queue`}
            disabled={inFlight}
            title={inFlight ? 'A screening is under way; pause the queue and try again.' : undefined}
            onClick={() => onAction(() => removeQueueItem(item.queue_id, item.id))}
          >
            <TrashIcon className="h-4 w-4" />
          </Button>
        </div>
      </td>
    </tr>
  )
}

/**
 * One queue: who is in it, what the worker has done so far, and the Start / Pause
 * / Resume control.
 *
 * The page never runs a screening itself — it only tells the backend whether the
 * background worker may claim this queue's items. Progress is read on demand
 * (Refresh) rather than polled, so an open tab is never load-bearing.
 */
export function QueueDetailPage() {
  const { queueId } = useParams<{ queueId: string }>()
  const id = Number(queueId)
  const navigate = useNavigate()

  const detail = useAsync<QueueDetail>(() => loadQueueDetail(id), [id])
  const [addOpen, setAddOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<unknown>) {
    setActionError(null)
    setBusy(true)
    try {
      await action()
      detail.reload()
    } catch (caught) {
      setActionError(toMessage(caught, 'Could not complete that action.'))
    } finally {
      setBusy(false)
    }
  }

  if (detail.loading) {
    return (
      <div className="card space-y-3 p-5">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }
  if (detail.error !== null) {
    // The queue's own name/position aren't known yet on a load failure, so a
    // full PageHeader isn't possible -- but losing navigation AND the ability
    // to retry without a manual refresh was the actual problem. This keeps
    // both: a way back to the list, and a way to try the same load again.
    return (
      <>
        <PageHeader title="Queue" backTo="/queues" backLabel="All queues" />
        <ErrorState message={detail.error} onRetry={() => detail.reload()} />
      </>
    )
  }
  if (detail.data === null) return null

  const { queue, position, items, candidates, results } = detail.data
  const queuedIds = new Set(items.map((item) => item.candidate_id))
  const candidateById = new Map(candidates.map((candidate) => [candidate.id, candidate]))
  const available = candidates.filter((candidate) => !queuedIds.has(candidate.id))
  const running = queue.status === 'running'

  return (
    <>
      <PageHeader
        title={queue.name}
        description={`Screening for ${position.title} — ${position.company_name}.`}
        backTo="/queues"
        backLabel="All queues"
        actions={
          <>
            <Button variant="secondary" onClick={() => detail.reload()} disabled={busy}>
              Refresh
            </Button>
            {running ? (
              <Button variant="secondary" disabled={busy} onClick={() => run(() => pauseQueue(id))}>
                Pause
              </Button>
            ) : (
              <Button
                disabled={busy || items.length === 0}
                title={items.length === 0 ? 'Add at least one candidate first.' : undefined}
                onClick={() =>
                  run(() => (queue.status === 'paused' ? resumeQueue(id) : startQueue(id)))
                }
              >
                {queue.status === 'paused' ? 'Resume' : 'Start'}
              </Button>
            )}
            <Button
              variant="danger"
              disabled={busy}
              onClick={() => run(async () => {
                await deleteQueue(id)
                navigate('/queues')
              })}
            >
              Delete queue
            </Button>
          </>
        }
      />

      {actionError !== null && <ErrorAlert message={actionError} className="mb-4" />}

      <ProgressSummary detail={detail.data} />

      <div className="card overflow-hidden">
        <div className="border-ink-200 flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <div>
            <h3 className="text-ink-900 text-sm font-semibold">Candidates in this queue</h3>
            <p className="text-ink-500 mt-0.5 text-xs">
              Worked one at a time by the background worker, whether or not this page is open.
            </p>
          </div>
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <PlusIcon className="h-4 w-4" />
            Add candidate
          </Button>
        </div>

        {items.length === 0 ? (
          <EmptyState
            icon={<QueueIcon className="h-10 w-10" />}
            title="No candidates in this queue"
            description="Add candidates whose interview plan you have already approved, then start the queue."
            action={
              <Button onClick={() => setAddOpen(true)}>
                <PlusIcon className="h-4 w-4" />
                Add candidate
              </Button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            {/* Kept as a hand-rolled table rather than the DataTable primitive:
                each row (ItemRow, below) carries its own local state and an
                async action per candidate, which does not fit DataTable's
                stateless column-render API without re-implementing that
                per-row logic -- out of scope for a markup-only pass. */}
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="bg-surface-sunken text-ink-600 border-border-default border-b text-xs font-semibold tracking-wide uppercase">
                <tr>
                  <th className="px-5 py-3 font-semibold">Candidate</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                  <th className="px-5 py-3 font-semibold">Attempts</th>
                  <th className="px-5 py-3 font-semibold">Result</th>
                  <th className="px-5 py-3 font-semibold">Detail</th>
                  <th className="px-5 py-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-border-subtle divide-y">
                {items.map((item) => (
                  <ItemRow
                    key={item.id}
                    item={item}
                    candidate={candidateById.get(item.candidate_id)}
                    result={results[item.id]}
                    onAction={run}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={addOpen}
        title="Add candidate to queue"
        description="Only candidates on this position can be added."
        onClose={() => setAddOpen(false)}
      >
        <AddCandidateForm
          available={available}
          onAdd={async (candidateId) => {
            await addQueueItem(id, candidateId)
            setAddOpen(false)
            detail.reload()
          }}
        />
      </Modal>
    </>
  )
}
