import { useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'

import { getCandidate, getCandidateResult, updateCandidate } from '@/api/candidates'
import { prepareInterview } from '@/api/interviews'
import { getPosition } from '@/api/positions'
import { listQuestions } from '@/api/questions'
import type {
  Candidate,
  CandidateStatus,
  Position,
  PrepareInterviewResult,
  Question,
  ScreeningResult,
} from '@/api/types'
import { getInterviewPlan } from '@/api/interviewPlans'
import type { CandidateInterviewPlan } from '@/api/types'
import { CandidateStageTracker, type Stage, type StageStatus } from '@/components/CandidateStageTracker'
import { CvUploadCard } from '@/components/CvUploadCard'
import { InterviewMediaPanel } from '@/components/InterviewMediaPanel'
import { ScreeningResultPanel } from '@/components/ScreeningResultPanel'
import { InterviewPlanEditor } from '@/components/InterviewPlanEditor'
import { InterviewPlanPanel } from '@/components/InterviewPlanPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge, CandidateStatusBadge, ScreeningOutcomeBadge } from '@/components/ui/Badge'
import { CANDIDATE_STATUS_LABEL } from '@/lib/labels'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'
import { ErrorAlert, LoadingBlock, Spinner } from '@/components/ui/Feedback'
import { PencilIcon, SparkIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { Tabs, TabPanel, type TabItem } from '@/components/ui/Tabs'
import { toMessage, useAsync } from '@/lib/useAsync'

const STATUSES: CandidateStatus[] = [
  'new',
  'applied',
  'queued',
  'invited',
  'screening_in_progress',
  'screened',
  'withdrawn',
]

// The linear path a candidate's status normally advances through. 'withdrawn'
// is deliberately excluded -- it can happen from any point, not a rung on
// this ladder, so it is handled separately wherever this order is used.
// 'applied' and 'invited' are the auto-pipeline-only rungs a manually-added
// candidate simply skips over (statusAtLeast still orders correctly for them,
// since a manual candidate goes straight from 'new' to 'queued').
const STATUS_ORDER: CandidateStatus[] = [
  'new', 'applied', 'queued', 'invited', 'screening_in_progress', 'screened',
]

function statusAtLeast(status: CandidateStatus, target: CandidateStatus): boolean {
  return STATUS_ORDER.indexOf(status) >= STATUS_ORDER.indexOf(target)
}

const TABS: TabItem[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'plan', label: 'Interview Plan' },
  { value: 'media', label: 'Recording & Transcript' },
  { value: 'evaluation', label: 'Evaluation' },
]

function EditCandidateForm({
  candidate,
  onSaved,
}: {
  candidate: Candidate
  onSaved: () => void
}) {
  const [fullName, setFullName] = useState(candidate.full_name)
  const [email, setEmail] = useState(candidate.email ?? '')
  const [phone, setPhone] = useState(candidate.phone ?? '')
  const [cvText, setCvText] = useState(candidate.cv_text ?? '')
  const [status, setStatus] = useState<CandidateStatus>(candidate.status)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await updateCandidate(candidate.id, {
        full_name: fullName,
        email: email.trim() === '' ? null : email,
        phone: phone.trim() === '' ? null : phone,
        cv_text: cvText.trim() === '' ? null : cvText,
        status,
      })
      onSaved()
    } catch (caught) {
      setError(toMessage(caught, 'Could not save the candidate.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <TextField
        label="Full name"
        required
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <TextField label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
      </div>
      <SelectField
        label="Status"
        value={status}
        onChange={(e) => setStatus(e.target.value as CandidateStatus)}
        hint="A screening status only. It never represents a hiring decision."
      >
        {STATUSES.map((option) => (
          <option key={option} value={option}>
            {CANDIDATE_STATUS_LABEL[option]}
          </option>
        ))}
      </SelectField>
      <TextAreaField
        label="CV text"
        value={cvText}
        onChange={(e) => setCvText(e.target.value)}
        className="min-h-32"
        hint="Optional."
      />
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          Save changes
        </Button>
      </div>
    </form>
  )
}

export function CandidateDetailPage() {
  const { candidateId } = useParams<{ candidateId: string }>()
  const id = Number(candidateId)

  const [editOpen, setEditOpen] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [prepareError, setPrepareError] = useState<string | null>(null)
  const [plan, setPlan] = useState<PrepareInterviewResult | null>(null)
  const [tab, setTab] = useState('overview')

  const candidate = useAsync<Candidate>(() => getCandidate(id), [id])
  const positionId = candidate.data?.position_id
  const position = useAsync<Position | null>(
    () => (positionId === undefined ? Promise.resolve(null) : getPosition(positionId)),
    [positionId],
  )
  const questions = useAsync<Question[]>(
    () => (positionId === undefined ? Promise.resolve([]) : listQuestions(positionId)),
    [positionId],
  )
  const interviewPlan = useAsync<CandidateInterviewPlan | null>(
    () => getInterviewPlan(id),
    [id],
  )
  const screeningResult = useAsync<ScreeningResult | null>(
    () => getCandidateResult(id),
    [id],
  )

  async function handlePrepare() {
    if (!candidate.data) return
    setPrepareError(null)
    setPreparing(true)
    try {
      const result = await prepareInterview(candidate.data.position_id, candidate.data.id)
      setPlan(result)
    } catch (caught) {
      setPrepareError(toMessage(caught, 'Could not prepare the interview.'))
    } finally {
      setPreparing(false)
    }
  }

  if (candidate.loading) return <LoadingBlock />
  if (candidate.error) return <ErrorAlert message={candidate.error} />
  if (!candidate.data) return null

  const current = candidate.data
  const questionCount = (questions.data ?? []).length
  const canPrepare = questionCount > 0

  // ---- Stage tracker + "what's next", derived entirely from data already
  // loaded on this page. No backend state is invented: each flag below reads
  // an existing field (candidate.status, the plan's own status/questions,
  // whether a screening result exists). See the Phase 5 plan for the full
  // reasoning behind each mapping.
  const hasCv = current.cv_text !== null && current.cv_text !== ''
  // Same condition InterviewPlanEditor itself uses to decide between its
  // empty state and the real editor (plan === null) -- kept identical so the
  // tracker, the next-action line, and the Prepare gating below never disagree
  // about whether a plan "exists".
  const hasPlan = interviewPlan.data !== null
  const planApproved = interviewPlan.data?.status === 'approved'
  const isQueued = statusAtLeast(current.status, 'queued')
  const isInterviewed =
    statusAtLeast(current.status, 'screening_in_progress') || screeningResult.data !== null
  const isEvaluated = current.status === 'screened' || screeningResult.data !== null

  // Once the interview has actually started, the plan stops being an editable
  // draft and becomes the record of what this candidate was screened against.
  // Preparing, regenerating or editing it from here on would rewrite history
  // for an interview that already used the old version, so every one of those
  // actions is withdrawn while the plan itself stays fully visible.
  //
  // Both halves of this come from backend state already loaded on this page --
  // the candidate's own status and whether a screening report exists -- so the
  // lock can never disagree with what the backend thinks happened.
  const interviewLocked = isInterviewed
  const interviewLockReason = isEvaluated
    ? 'This interview is complete. The plan is kept as a record of what the candidate was screened against and can no longer be edited.'
    : 'This interview has already started. The plan is locked to the questions being asked and can no longer be edited.'

  const stageFlags = [true, hasCv, hasPlan, planApproved, isQueued, isInterviewed, isEvaluated, isEvaluated]
  const stageLabels = ['Added', 'CV', 'Plan', 'Approved', 'Queued', 'Interviewed', 'Evaluated', 'Decision']
  // What the tracker highlights as "current" and what the next-action line
  // says must agree, so both derive from this one priority order rather than
  // two separate computations. CV is deliberately absent from it -- it is a
  // trackable milestone but never actually blocks anything downstream (a plan
  // can be generated with no CV on file), so it should read as complete or
  // upcoming, never as the thing HR is being told to do next.
  //
  // The two terminal states are checked FIRST, ahead of the plan/approval
  // rungs. An interview that has already run but whose plan row is missing is
  // an inconsistent state, and the ladder must report where the candidate
  // actually is rather than telling HR to go generate a plan for a screening
  // that already happened.
  const nextStage: { key: string; text: string } =
    current.status === 'withdrawn'
      ? { key: 'added', text: 'This candidate withdrew from consideration.' }
      : isEvaluated
        ? { key: 'decision', text: 'Screening complete — review the evaluation below.' }
        : isInterviewed
          ? { key: 'evaluated', text: 'Interview is currently in progress.' }
          : !hasPlan
            ? { key: 'plan', text: 'Generate or write an interview plan for this candidate.' }
            : !planApproved
              ? { key: 'approved', text: 'Review and approve the interview plan.' }
              : !isQueued
                ? { key: 'queued', text: 'Add this candidate to a calling queue to begin screening.' }
                : { key: 'interviewed', text: 'Waiting for the queued interview to run.' }

  const stages: Stage[] = stageLabels.map((label, index) => {
    const key = label.toLowerCase()
    const status: StageStatus = stageFlags[index]
      ? 'complete'
      : key === nextStage.key
        ? 'current'
        : 'upcoming'
    return { key, label, status }
  })

  // Prepare interview is only ever meaningful once a plan exists AND has been
  // explicitly approved -- generating/editing a plan and preparing the actual
  // candidate-specific interview are deliberately separate, reviewable steps.
  // A single reason string drives every "Prepare interview" control on this
  // page, so the toolbar button, the empty-state card, and their disabled
  // states can never drift out of sync with each other.
  const prepareDisabledReason: string | null = !canPrepare
    ? 'Add interview questions to this position first.'
    : !planApproved
      ? 'Approve the interview plan before preparing the interview.'
      : null
  const canPrepareNow = prepareDisabledReason === null

  // Local to this tab: the four-step path from a saved plan to a running
  // interview the user asked to see reinforced. Distinct from the page-level
  // stage tracker above (which covers the candidate's whole journey) -- this
  // one exists purely to make Plan -> Approve -> Prepare -> Queue/Interview
  // legible at the point where HR is deciding what to do next.
  // Once the interview has run, Prepare and Queue are settled facts regardless
  // of whether this particular page visit happened to call Prepare (`plan` is
  // ephemeral local state, so it is null on every fresh load).
  const planFlowFlags = [
    hasPlan,
    planApproved,
    interviewLocked || plan !== null,
    interviewLocked || isQueued,
  ]
  const planFlowLabels = ['Plan', 'Approve', 'Prepare', 'Queue / Interview']
  const planFlowFirstIncomplete = planFlowFlags.findIndex((done) => !done)
  const planFlowStages: Stage[] = planFlowLabels.map((label, index) => ({
    key: `flow-${index}`,
    label,
    status: planFlowFlags[index] ? 'complete' : index === planFlowFirstIncomplete ? 'current' : 'upcoming',
  }))

  const planBadge = interviewPlan.loading ? (
    <span className="text-ink-400 text-sm">—</span>
  ) : !hasPlan ? (
    <Badge tone="neutral">Not started</Badge>
  ) : planApproved ? (
    <Badge tone="success">Approved</Badge>
  ) : (
    <Badge tone="warning">Draft</Badge>
  )

  const evaluationBadge = screeningResult.loading ? (
    <span className="text-ink-400 text-sm">—</span>
  ) : screeningResult.data !== null ? (
    <ScreeningOutcomeBadge outcome={screeningResult.data.screening_outcome} />
  ) : (
    <Badge tone="neutral">Not yet evaluated</Badge>
  )

  return (
    <>
      <PageHeader
        backTo="/candidates"
        backLabel="All candidates"
        title={current.full_name}
        description={position.data ? `${position.data.title} · ${position.data.company_name}` : undefined}
        actions={
          <>
            {/* Named to distinguish it from "Edit interview plan" below -- this one
                only changes the candidate's own details. */}
            <Button variant="secondary" onClick={() => setEditOpen(true)}>
              <PencilIcon className="h-4 w-4" />
              Edit candidate details
            </Button>
          </>
        }
      />

      {!canPrepare && !questions.loading && !interviewLocked && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          This position has no interview questions yet.{' '}
          {position.data && (
            <Link to={`/positions/${position.data.id}`} className="font-medium underline">
              Add questions
            </Link>
          )}{' '}
          before preparing an interview.
        </div>
      )}

      <Card className="mb-6 p-5">
        <CandidateStageTracker stages={stages} />
        <p className="text-ink-700 mt-4 text-sm">
          <span className="text-ink-500 font-medium">Next: </span>
          {nextStage.text}
        </p>
      </Card>

      <Tabs idPrefix="candidate-detail" items={TABS} value={tab} onChange={setTab} />

      <div className="mt-6">
        <TabPanel idPrefix="candidate-detail" value="overview" active={tab === 'overview'}>
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-6">
              <section className="card space-y-4 p-5">
                <h3 className="text-ink-900 text-sm font-semibold">Candidate details</h3>
                <dl className="space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-ink-500">Status</dt>
                    <dd>
                      <CandidateStatusBadge status={current.status} />
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-ink-500">Email</dt>
                    <dd className="text-ink-800 truncate">{current.email ?? '—'}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-ink-500">Phone</dt>
                    <dd className="text-ink-800">{current.phone ?? '—'}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-ink-500">Position</dt>
                    <dd className="text-ink-800 truncate">
                      {position.data ? (
                        <Link
                          to={`/positions/${position.data.id}`}
                          className="text-brand-700 hover:underline"
                        >
                          {position.data.title}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-ink-500">Questions</dt>
                    <dd className="text-ink-800 tabular-nums">{questionCount}</dd>
                  </div>
                </dl>
              </section>

              <CvUploadCard
                candidate={current}
                onUpdated={(updated) => candidate.setData(updated)}
                readOnly={interviewLocked}
              />
            </div>

            <Card className="p-5">
              <h3 className="text-ink-900 text-sm font-semibold">Interview &amp; evaluation</h3>
              <dl className="mt-4 space-y-4 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Interview plan</dt>
                  <dd>{planBadge}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Evaluation</dt>
                  <dd>{evaluationBadge}</dd>
                </div>
              </dl>
              <div className="border-border-subtle mt-5 flex flex-wrap gap-x-5 gap-y-2 border-t pt-4">
                <button
                  type="button"
                  onClick={() => setTab('plan')}
                  className="text-brand-700 text-sm font-medium hover:underline"
                >
                  Open interview plan
                </button>
                {screeningResult.data !== null && (
                  <>
                    <button
                      type="button"
                      onClick={() => setTab('evaluation')}
                      className="text-brand-700 text-sm font-medium hover:underline"
                    >
                      Open evaluation
                    </button>
                    <button
                      type="button"
                      onClick={() => setTab('media')}
                      className="text-brand-700 text-sm font-medium hover:underline"
                    >
                      Open recording &amp; transcript
                    </button>
                  </>
                )}
              </div>
            </Card>
          </div>
        </TabPanel>

        <TabPanel idPrefix="candidate-detail" value="plan" active={tab === 'plan'} className="space-y-6">
          {prepareError && <ErrorAlert message={prepareError} />}

          <div className="bg-surface-sunken rounded-lg px-4 py-3.5">
            <CandidateStageTracker stages={planFlowStages} />
          </div>

          {!interviewPlan.loading && (
            <InterviewPlanEditor
              candidateId={id}
              plan={interviewPlan.data ?? null}
              onPlanChange={(updated) => interviewPlan.setData(updated)}
              readOnly={interviewLocked}
              readOnlyReason={interviewLockReason}
              headerActions={
                interviewLocked ? undefined : (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handlePrepare}
                      disabled={preparing || !canPrepareNow}
                    >
                      {preparing ? (
                        <Spinner className="h-4 w-4" />
                      ) : (
                        <SparkIcon className="h-4 w-4" />
                      )}
                      {preparing ? 'Preparing…' : 'Prepare interview'}
                    </Button>
                    {!preparing && prepareDisabledReason && (
                      <span className="text-ink-500 text-xs">{prepareDisabledReason}</span>
                    )}
                  </div>
                )
              }
            />
          )}

          {plan ? (
            <InterviewPlanPanel result={plan} />
          ) : hasPlan && !interviewLocked ? (
            // Only shown once a plan actually exists -- with no plan at all,
            // InterviewPlanEditor's own empty state above is the single,
            // unambiguous call to action ("Generate interview plan"), so this
            // card stays hidden rather than showing a second, competing action.
            // It is also withdrawn entirely once the interview has run: "No
            // interview prepared yet" is false there, and preparing again
            // would create a second session for an interview already held.
            <div
              className="card flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:p-6"
              data-testid="prepared-interview-empty-state"
            >
              <div className="bg-brand-50 text-brand-600 flex h-11 w-11 shrink-0 items-center justify-center rounded-xl">
                <SparkIcon className="h-6 w-6" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-ink-900 text-base font-semibold">No interview prepared yet</h3>
                <p className="text-ink-500 mt-1 max-w-xl text-sm leading-5">
                  Prepare the candidate-specific interview from the current plan and available CV
                  details.
                </p>
                {prepareDisabledReason && (
                  <p className="text-warning-700 mt-2 text-xs font-medium">{prepareDisabledReason}</p>
                )}
              </div>
              <Button
                className="w-full shrink-0 sm:w-auto"
                onClick={handlePrepare}
                disabled={preparing || !canPrepareNow}
              >
                {preparing ? (
                  <Spinner className="h-4 w-4 border-white/40 border-t-white" />
                ) : (
                  <SparkIcon className="h-4 w-4" />
                )}
                {preparing ? 'Preparing…' : 'Prepare interview'}
              </Button>
            </div>
          ) : null}
        </TabPanel>

        <TabPanel idPrefix="candidate-detail" value="media" active={tab === 'media'}>
          {screeningResult.loading ? (
            <div className="card">
              <LoadingBlock label="Loading recording…" />
            </div>
          ) : screeningResult.data !== null ? (
            // Keyed off the report's session_id, the only place the HR API
            // exposes one. Recruiter view only -- the candidate interview
            // screen never renders this.
            <div className="card p-5">
              <InterviewMediaPanel sessionId={screeningResult.data.session_id} />
            </div>
          ) : (
            <div className="card p-5">
              <h3 className="text-ink-900 text-sm font-semibold">Recording &amp; transcript</h3>
              <p className="text-ink-500 mt-1 text-sm">
                Available once this candidate has completed an interview.
              </p>
            </div>
          )}
        </TabPanel>

        <TabPanel idPrefix="candidate-detail" value="evaluation" active={tab === 'evaluation'}>
          {screeningResult.loading ? (
            <div className="card">
              <LoadingBlock label="Loading HR report…" />
            </div>
          ) : screeningResult.error !== null ? (
            <ErrorAlert message={screeningResult.error} />
          ) : screeningResult.data !== null ? (
            <ScreeningResultPanel result={screeningResult.data} />
          ) : (
            <div className="card p-5">
              <h3 className="text-ink-900 text-sm font-semibold">HR interview report</h3>
              <p className="text-ink-500 mt-1 text-sm">
                No completed interview report is available for this candidate yet.
              </p>
            </div>
          )}
        </TabPanel>
      </div>

      <Modal open={editOpen} title="Edit candidate" onClose={() => setEditOpen(false)}>
        <EditCandidateForm
          candidate={current}
          onSaved={() => {
            setEditOpen(false)
            candidate.reload()
          }}
        />
      </Modal>
    </>
  )
}
