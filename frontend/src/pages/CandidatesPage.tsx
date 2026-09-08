import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { createCandidate, listAllCandidates, uploadCandidateCv } from '@/api/candidates'
import { listPositions } from '@/api/positions'
import type { CandidateWithPosition } from '@/api/candidates'
import type { Position } from '@/api/types'
import { CvFilePicker } from '@/components/CvFilePicker'
import { PageHeader } from '@/components/layout/PageHeader'
import { Avatar } from '@/components/ui/Avatar'
import { CandidateStatusBadge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'
import {
  EmptyState,
  ErrorAlert,
  ErrorState,
  Skeleton,
  Spinner,
  SuccessAlert,
} from '@/components/ui/Feedback'
import { PlusIcon, UsersIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { validateCvFile } from '@/lib/cvFile'
import { toMessage, useAsync } from '@/lib/useAsync'

/** Outcome of the add flow, so the page can report a partial success honestly. */
export interface AddCandidateOutcome {
  candidateName: string
  cvUploadError: string | null
  cvFilename: string | null
}

function AddCandidateForm({
  positions,
  onCreated,
}: {
  positions: Position[]
  onCreated: (outcome: AddCandidateOutcome) => void
}) {
  const [positionId, setPositionId] = useState<string>(
    positions.length > 0 ? String(positions[0]!.id) : '',
  )
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [cvFile, setCvFile] = useState<File | null>(null)
  // Set when a chosen file was rejected. Kept separate from `error` so an
  // unresolved file problem can block submission -- otherwise HR picks a bad
  // file, sees the message, hits Add, and silently gets a candidate with no CV.
  const [fileError, setFileError] = useState<string | null>(null)
  const [cvText, setCvText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function handleSelectFile(file: File | null) {
    setError(null)
    if (file === null) {
      setCvFile(null)
      setFileError(null)
      return
    }
    const problem = validateCvFile(file)
    setCvFile(problem === null ? file : null)
    setFileError(problem)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (positionId === '') {
      setError('Select a position for this candidate.')
      return
    }
    // Refuse to create anything while a chosen file is still unusable, so no
    // candidate is left behind without the CV that was meant to accompany it.
    if (fileError !== null) {
      setError(`${fileError} Choose a different file, or remove it to continue without a CV.`)
      return
    }

    setSubmitting(true)
    let created: Awaited<ReturnType<typeof createCandidate>>
    try {
      created = await createCandidate({
        position_id: Number(positionId),
        full_name: fullName,
        email: email.trim() === '' ? null : email,
        phone: phone.trim() === '' ? null : phone,
        cv_text: cvText.trim() === '' ? null : cvText,
      })
    } catch (caught) {
      setError(toMessage(caught, 'Could not add the candidate.'))
      setSubmitting(false)
      return
    }

    // The upload endpoint needs an id, so this is necessarily a second step.
    // If it fails the candidate still exists, so we report that rather than
    // pretending nothing happened -- retrying the whole form would duplicate them.
    let cvUploadError: string | null = null
    if (cvFile !== null) {
      try {
        await uploadCandidateCv(created.id, cvFile)
      } catch (caught) {
        cvUploadError = toMessage(caught, 'Could not upload the CV.')
      }
    }

    setSubmitting(false)
    onCreated({
      candidateName: created.full_name,
      cvUploadError,
      cvFilename: cvUploadError === null && cvFile !== null ? cvFile.name : null,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <SelectField
        label="Position"
        required
        value={positionId}
        onChange={(e) => setPositionId(e.target.value)}
      >
        <option value="">Select a position…</option>
        {positions.map((position) => (
          <option key={position.id} value={position.id}>
            {position.title} — {position.company_name}
          </option>
        ))}
      </SelectField>
      <TextField
        label="Full name"
        required
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
        placeholder="Jordan Rivera"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="jordan@example.com"
        />
        <TextField
          label="Phone"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+1 555 0100"
        />
      </div>
      <CvFilePicker
        label="CV file"
        file={cvFile}
        onSelect={handleSelectFile}
        disabled={submitting}
      />

      <details className="group" open={cvText !== ''}>
        <summary className="text-ink-600 hover:text-ink-900 cursor-pointer text-sm font-medium">
          Or paste CV text instead
        </summary>
        <div className="mt-3">
          <TextAreaField
            label="CV text"
            value={cvText}
            onChange={(e) => setCvText(e.target.value)}
            placeholder="Paste the candidate's CV text here…"
            hint="Optional fallback for when you only have the text. A chosen file takes precedence."
            className="min-h-32"
          />
        </div>
      </details>

      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          {submitting && cvFile !== null ? 'Adding and parsing CV…' : 'Add candidate'}
        </Button>
      </div>
    </form>
  )
}

export function CandidatesPage() {
  const [modalOpen, setModalOpen] = useState(false)
  const [positionFilter, setPositionFilter] = useState<string>('')
  const [notice, setNotice] = useState<AddCandidateOutcome | null>(null)

  const positions = useAsync<Position[]>(() => listPositions(), [])
  const candidates = useAsync<CandidateWithPosition[]>(() => listAllCandidates(), [])

  const loading = positions.loading || candidates.loading
  const error = positions.error ?? candidates.error
  const positionList = positions.data ?? []
  const allRows = candidates.data ?? []
  const rows =
    positionFilter === ''
      ? allRows
      : allRows.filter((row) => String(row.position.id) === positionFilter)

  function reloadAll() {
    positions.reload()
    candidates.reload()
  }

  const columns: DataTableColumn<CandidateWithPosition>[] = [
    {
      key: 'candidate',
      header: 'Candidate',
      render: ({ candidate }) => (
        <Link to={`/candidates/${candidate.id}`} className="hover:text-brand-700 flex items-center gap-2.5">
          <Avatar name={candidate.full_name} size="sm" />
          <span className="text-ink-900 font-medium">{candidate.full_name}</span>
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
      key: 'contact',
      header: 'Contact',
      className: 'text-ink-600',
      render: ({ candidate }) => candidate.email ?? <span className="text-ink-400">—</span>,
    },
    {
      key: 'cv',
      header: 'CV',
      render: ({ candidate }) =>
        candidate.cv_text ? (
          <span className="text-success-700">Provided</span>
        ) : (
          <span className="text-ink-400">Not provided</span>
        ),
    },
    {
      key: 'status',
      header: 'Status',
      render: ({ candidate }) => <CandidateStatusBadge status={candidate.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title="Candidates"
        description="Everyone in your screening pipeline, across all positions."
        actions={
          <Button
            onClick={() => {
              setNotice(null)
              setModalOpen(true)
            }}
            disabled={positionList.length === 0}
          >
            <PlusIcon className="h-4 w-4" />
            Add candidate
          </Button>
        }
      />

      {notice !== null &&
        (notice.cvUploadError === null ? (
          <SuccessAlert
            className="mb-4"
            message={
              notice.cvFilename === null
                ? `${notice.candidateName} was added.`
                : `${notice.candidateName} was added and “${notice.cvFilename}” was parsed.`
            }
          />
        ) : (
          // The candidate exists; only the CV failed. Say so precisely, so nobody
          // re-adds them looking for a CV that simply needs re-uploading.
          <div
            role="alert"
            className="border-warning-200 bg-warning-50 text-warning-800 mb-4 rounded-lg border px-4 py-3 text-sm"
          >
            {notice.candidateName} was added, but the CV could not be uploaded:{' '}
            {notice.cvUploadError} You can upload it from their profile.
          </div>
        ))}

      {positionList.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <label htmlFor="position-filter" className="text-ink-600 text-sm font-medium">
              Position
            </label>
            <select
              id="position-filter"
              value={positionFilter}
              onChange={(e) => setPositionFilter(e.target.value)}
              className="border-border-default text-ink-800 bg-surface-raised max-w-xs rounded-lg border px-3 py-1.5 text-sm"
            >
              <option value="">All positions</option>
              {positionList.map((position) => (
                <option key={position.id} value={position.id}>
                  {position.title}
                </option>
              ))}
            </select>
          </div>
          {!loading && !error && (
            <span className="text-ink-400 text-sm">
              {rows.length} {rows.length === 1 ? 'candidate' : 'candidates'}
            </span>
          )}
        </div>
      )}

      {loading && (
        <div className="card space-y-3 p-5">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}
      {error && <ErrorState message={error} onRetry={reloadAll} />}

      {!loading && !error && positionList.length === 0 && (
        <div className="card">
          <EmptyState
            icon={<UsersIcon className="h-10 w-10" />}
            title="Create a position first"
            description="Candidates belong to a position, so you need at least one position before adding candidates."
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
            icon={<UsersIcon className="h-10 w-10" />}
            title="No candidates yet"
            description="Add a candidate to start screening. A CV is optional."
            action={
              <Button onClick={() => setModalOpen(true)}>
                <PlusIcon className="h-4 w-4" />
                Add candidate
              </Button>
            }
          />
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <div className="card overflow-hidden">
          <DataTable
            caption="Candidates"
            columns={columns}
            rows={rows}
            rowKey={({ candidate }) => candidate.id}
          />
        </div>
      )}

      <Modal
        open={modalOpen}
        title="Add candidate"
        description="Attach the candidate to one of your positions."
        onClose={() => setModalOpen(false)}
      >
        <AddCandidateForm
          positions={positionList}
          onCreated={(outcome) => {
            setModalOpen(false)
            setNotice(outcome)
            reloadAll()
          }}
        />
      </Modal>
    </>
  )
}
