import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { listAgentConfigs } from '@/api/agentConfigs'
import { listCandidates } from '@/api/candidates'
import { deletePosition, getPosition, updatePosition } from '@/api/positions'
import {
  addQuestion,
  deleteQuestion,
  listQuestions,
  reorderQuestions,
  updateQuestion,
} from '@/api/questions'
import type {
  AgentConfig,
  Candidate,
  Position,
  PositionStatus,
  Question,
  QuestionCategory,
  VoiceAgentPersona,
} from '@/api/types'
import { PipelineBoard } from '@/components/PipelineBoard'
import { ScreeningSetupPanel } from '@/components/ScreeningSetupPanel'
import { SuggestQuestionsModal } from '@/components/SuggestQuestionsModal'
import { PageHeader } from '@/components/layout/PageHeader'
import {
  Badge,
  CandidateStatusBadge,
  CategoryBadge,
  PositionStatusBadge,
} from '@/components/ui/Badge'
import { QUESTION_CATEGORY_LABEL } from '@/lib/labels'
import { Button } from '@/components/ui/Button'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'
import { EmptyState, ErrorAlert, LoadingBlock, Spinner } from '@/components/ui/Feedback'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  DocumentIcon,
  PencilIcon,
  PlusIcon,
  RobotIcon,
  SparkIcon,
  TrashIcon,
  UsersIcon,
} from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { Tabs, TabPanel, type TabItem } from '@/components/ui/Tabs'
import { toMessage, useAsync } from '@/lib/useAsync'

const CATEGORIES: QuestionCategory[] = [
  'candidate_background',
  'technical',
  'problem_solving',
  'behavioral',
]
const DIFFICULTIES = ['easy', 'medium', 'hard'] as const
const STATUSES: PositionStatus[] = ['draft', 'active', 'closed']
const EXPERIENCE_LEVELS = ['Intern', 'Junior', 'Mid-Level', 'Senior', 'Lead', 'Principal', 'Executive']

const TABS: TabItem[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'questions', label: 'Questions' },
  { value: 'candidates', label: 'Candidates' },
  { value: 'agent', label: 'Agent' },
  { value: 'screening', label: 'Screening setup' },
]

/** A config is a voice persona when it carries the identity fields the backend
 *  validates against; older/unrelated blobs are not summarized here. Mirrors
 *  the same check AgentConfigPage uses, kept local since it's three lines and
 *  not worth coupling two pages over. */
function asPersona(config: Record<string, unknown>): VoiceAgentPersona | null {
  return typeof config.agent_name === 'string' && typeof config.company_name === 'string'
    ? (config as unknown as VoiceAgentPersona)
    : null
}

interface QuestionFormValues {
  category: QuestionCategory | ''
  question: string
  purpose: string
  expectedTopics: string
  difficulty: string
  followUpAllowed: boolean
}

const EMPTY_QUESTION: QuestionFormValues = {
  // Deliberately blank: category is required by the backend and must be an
  // explicit HR choice, never silently defaulted to a category the agent would
  // then use to route this question's evidence during evaluation.
  category: '',
  question: '',
  purpose: '',
  expectedTopics: '',
  difficulty: 'medium',
  followUpAllowed: true,
}

function QuestionForm({
  initial,
  submitLabel,
  onSubmit,
}: {
  initial: QuestionFormValues
  submitLabel: string
  onSubmit: (values: QuestionFormValues) => Promise<void>
}) {
  const [values, setValues] = useState<QuestionFormValues>(initial)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (values.category === '') {
      setError('Choose a question category. It is required and is never inferred automatically.')
      return
    }
    setSubmitting(true)
    try {
      await onSubmit(values)
    } catch (caught) {
      setError(toMessage(caught, 'Could not save the question.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {error && <ErrorAlert message={error} />}
      <TextAreaField
        label="Question"
        required
        value={values.question}
        onChange={(e) => setValues({ ...values, question: e.target.value })}
        placeholder="Explain the retrieval architecture you used in your last project."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Category"
          required
          value={values.category}
          onChange={(e) => setValues({ ...values, category: e.target.value as QuestionCategory })}
          hint="Required — determines how this answer is evaluated."
        >
          <option value="">Select a category…</option>
          {CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {QUESTION_CATEGORY_LABEL[category]}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Difficulty"
          value={values.difficulty}
          onChange={(e) => setValues({ ...values, difficulty: e.target.value })}
        >
          {DIFFICULTIES.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </SelectField>
      </div>
      <TextField
        label="Purpose"
        value={values.purpose}
        onChange={(e) => setValues({ ...values, purpose: e.target.value })}
        placeholder="Assess depth of retrieval knowledge."
        hint="Optional. Why this question is being asked."
      />
      <TextField
        label="Expected topics"
        value={values.expectedTopics}
        onChange={(e) => setValues({ ...values, expectedTopics: e.target.value })}
        placeholder="retrieval, vector database, reranking"
        hint="Optional, comma-separated. Never shown to the candidate."
      />
      <label className="flex items-center gap-2.5 text-sm">
        <input
          type="checkbox"
          checked={values.followUpAllowed}
          onChange={(e) => setValues({ ...values, followUpAllowed: e.target.checked })}
          className="border-ink-300 text-brand-600 h-4 w-4 rounded"
        />
        <span className="text-ink-700">Allow the agent to ask follow-up questions</span>
      </label>
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}

function parseTopics(raw: string): string[] {
  return raw
    .split(',')
    .map((topic) => topic.trim())
    .filter((topic) => topic.length > 0)
}

function EditPositionForm({
  position,
  onSaved,
}: {
  position: Position
  onSaved: () => void
}) {
  const [title, setTitle] = useState(position.title)
  const [companyName, setCompanyName] = useState(position.company_name)
  const [description, setDescription] = useState(position.description ?? '')
  const [experienceLevel, setExperienceLevel] = useState(position.experience_level ?? '')
  const [threshold, setThreshold] = useState(
    position.pass_score_threshold === null ? '' : String(position.pass_score_threshold),
  )
  const [status, setStatus] = useState<PositionStatus>(position.status)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await updatePosition(position.id, {
        title,
        company_name: companyName,
        description: description.trim() === '' ? null : description,
        experience_level: experienceLevel === '' ? null : experienceLevel,
        pass_score_threshold: threshold === '' ? null : Number(threshold),
        status,
      })
      onSaved()
    } catch (caught) {
      setError(toMessage(caught, 'Could not save the position.'))
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
        />
        <TextField
          label="Job title"
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <TextAreaField
        label="Job description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
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
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={submitting}>
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          Save changes
        </Button>
      </div>
    </form>
  )
}

export function PositionDetailPage() {
  const { positionId } = useParams<{ positionId: string }>()
  const id = Number(positionId)
  const navigate = useNavigate()

  const [addOpen, setAddOpen] = useState(false)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [editing, setEditing] = useState<Question | null>(null)
  const [editPositionOpen, setEditPositionOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('overview')

  const position = useAsync<Position>(() => getPosition(id), [id])
  const questions = useAsync<Question[]>(() => listQuestions(id), [id])
  const candidates = useAsync<Candidate[]>(() => listCandidates(id), [id])
  const agentConfigs = useAsync<AgentConfig[]>(() => listAgentConfigs(), [])

  async function runAction(action: () => Promise<unknown>, fallbackMessage: string) {
    setActionError(null)
    setBusy(true)
    try {
      await action()
      questions.reload()
    } catch (caught) {
      setActionError(toMessage(caught, fallbackMessage))
    } finally {
      setBusy(false)
    }
  }

  function move(list: Question[], index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= list.length) return
    const ordered = [...list]
    const moved = ordered[index]
    const swapped = ordered[target]
    if (!moved || !swapped) return
    ordered[index] = swapped
    ordered[target] = moved
    void runAction(
      () => reorderQuestions(id, ordered.map((q) => q.id)),
      'Could not reorder the questions.',
    )
  }

  async function handleDeletePosition() {
    if (!window.confirm('Delete this position? Its questions and candidates are removed too.')) {
      return
    }
    try {
      await deletePosition(id)
      navigate('/positions', { replace: true })
    } catch (caught) {
      setActionError(toMessage(caught, 'Could not delete the position.'))
    }
  }

  if (position.loading) return <LoadingBlock />
  if (position.error) return <ErrorAlert message={position.error} />
  if (!position.data) return null

  const current = position.data
  const questionList = questions.data ?? []
  const candidateList = candidates.data ?? []
  const ownConfigs = (agentConfigs.data ?? []).filter((config) => config.position_id === id)

  return (
    <>
      <PageHeader
        backTo="/positions"
        backLabel="All positions"
        title={current.title}
        description={current.company_name}
        actions={
          <>
            <Button variant="secondary" onClick={() => setEditPositionOpen(true)}>
              <PencilIcon className="h-4 w-4" />
              Edit
            </Button>
            <Button variant="danger" onClick={handleDeletePosition}>
              <TrashIcon className="h-4 w-4" />
              Delete
            </Button>
          </>
        }
      />

      {actionError && <ErrorAlert message={actionError} className="mb-4" />}

      <Tabs idPrefix="position-detail" items={TABS} value={tab} onChange={setTab} />

      <div className="mt-6">
        <TabPanel idPrefix="position-detail" value="overview" active={tab === 'overview'}>
          <div className="grid gap-6 lg:grid-cols-2">
            <section className="card p-5">
              <h3 className="text-ink-900 mb-4 text-sm font-semibold">Details</h3>
              <dl className="space-y-3 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Status</dt>
                  <dd>
                    <PositionStatusBadge status={current.status} />
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Experience</dt>
                  <dd className="text-ink-800">{current.experience_level ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Pass threshold</dt>
                  <dd className="text-ink-800 tabular-nums">
                    {current.pass_score_threshold ?? '—'}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Rubric profile</dt>
                  <dd className="text-ink-800">{current.rubric_profile ?? 'default'}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Questions</dt>
                  <dd className="text-ink-800 tabular-nums">{questionList.length}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-500">Candidates</dt>
                  <dd className="text-ink-800 tabular-nums">{candidateList.length}</dd>
                </div>
              </dl>
              {current.description && (
                <div className="border-ink-200 mt-4 border-t pt-4">
                  <dt className="text-ink-500 mb-1.5 text-sm">Description</dt>
                  <p className="text-ink-700 text-sm whitespace-pre-wrap">{current.description}</p>
                </div>
              )}
            </section>

            <section className="card p-5">
              <h3 className="text-ink-900 mb-4 text-sm font-semibold">Where things stand</h3>
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
                <button
                  type="button"
                  onClick={() => setTab('questions')}
                  className="text-brand-700 font-medium hover:underline"
                >
                  {questionList.length === 0
                    ? 'Add interview questions'
                    : `Review ${questionList.length} question${questionList.length === 1 ? '' : 's'}`}
                </button>
                <button
                  type="button"
                  onClick={() => setTab('candidates')}
                  className="text-brand-700 font-medium hover:underline"
                >
                  {candidateList.length === 0
                    ? 'No candidates yet'
                    : `${candidateList.length} candidate${candidateList.length === 1 ? '' : 's'} on this position`}
                </button>
                <button
                  type="button"
                  onClick={() => setTab('agent')}
                  className="text-brand-700 font-medium hover:underline"
                >
                  {ownConfigs.length === 0
                    ? 'No agent configuration scoped to this position'
                    : `${ownConfigs.length} agent configuration${ownConfigs.length === 1 ? '' : 's'} scoped here`}
                </button>
                <button
                  type="button"
                  onClick={() => setTab('screening')}
                  className="text-brand-700 font-medium hover:underline"
                >
                  Approve &amp; publish for public applications
                </button>
              </div>
            </section>
          </div>
        </TabPanel>

        <TabPanel idPrefix="position-detail" value="questions" active={tab === 'questions'}>
          <section className="card overflow-hidden">
            <div className="border-ink-200 flex items-center justify-between gap-3 border-b px-5 py-4">
              <div>
                <h3 className="text-ink-900 text-sm font-semibold">Interview questions</h3>
                <p className="text-ink-500 mt-0.5 text-xs">
                  Asked in this order. Every question needs an explicit category.
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button size="sm" variant="secondary" onClick={() => setSuggestOpen(true)}>
                  <SparkIcon className="h-4 w-4" />
                  Suggest with AI
                </Button>
                <Button size="sm" onClick={() => setAddOpen(true)}>
                  <PlusIcon className="h-4 w-4" />
                  Add
                </Button>
              </div>
            </div>

            {questions.loading && <LoadingBlock label="Loading questions…" />}
            {questions.error && <ErrorAlert message={questions.error} className="m-5" />}

            {!questions.loading && !questions.error && questionList.length === 0 && (
              <EmptyState
                icon={<DocumentIcon className="h-10 w-10" />}
                title="No questions yet"
                description="Add at least one question before preparing an interview for a candidate."
                action={
                  <div className="flex flex-wrap justify-center gap-2">
                    <Button onClick={() => setSuggestOpen(true)}>
                      <SparkIcon className="h-4 w-4" />
                      Suggest with AI
                    </Button>
                    <Button variant="secondary" onClick={() => setAddOpen(true)}>
                      <PlusIcon className="h-4 w-4" />
                      Add question
                    </Button>
                  </div>
                }
              />
            )}

            {questionList.length > 0 && (
              <ol className="divide-ink-100 divide-y">
                {questionList.map((question, index) => (
                  <li key={question.id} className="px-5 py-4">
                    <div className="flex items-start gap-3">
                      <span className="bg-ink-100 text-ink-600 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold tabular-nums">
                        {index + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-ink-900 text-sm">{question.question}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <CategoryBadge category={question.category} />
                          <span className="text-ink-500 text-xs capitalize">
                            {question.difficulty}
                          </span>
                          {question.follow_up_allowed && (
                            <span className="text-ink-500 text-xs">· follow-ups allowed</span>
                          )}
                        </div>
                        {question.purpose && (
                          <p className="text-ink-500 mt-2 text-xs">Purpose: {question.purpose}</p>
                        )}
                        {question.expected_topics.length > 0 && (
                          <p className="text-ink-500 mt-1 text-xs">
                            Expected topics: {question.expected_topics.join(', ')}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          type="button"
                          aria-label="Move question up"
                          disabled={index === 0 || busy}
                          onClick={() => move(questionList, index, -1)}
                          className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 rounded-md p-1.5 disabled:opacity-30 disabled:hover:bg-transparent"
                        >
                          <ArrowUpIcon className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          aria-label="Move question down"
                          disabled={index === questionList.length - 1 || busy}
                          onClick={() => move(questionList, index, 1)}
                          className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 rounded-md p-1.5 disabled:opacity-30 disabled:hover:bg-transparent"
                        >
                          <ArrowDownIcon className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          aria-label="Edit question"
                          onClick={() => setEditing(question)}
                          className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 rounded-md p-1.5"
                        >
                          <PencilIcon className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          aria-label="Delete question"
                          disabled={busy}
                          onClick={() =>
                            void runAction(
                              () => deleteQuestion(question.id),
                              'Could not delete the question.',
                            )
                          }
                          className="text-ink-400 rounded-md p-1.5 hover:bg-red-50 hover:text-red-600"
                        >
                          <TrashIcon className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </TabPanel>

        <TabPanel idPrefix="position-detail" value="candidates" active={tab === 'candidates'} className="space-y-6">
          <section>
            <h3 className="text-ink-900 mb-3 text-sm font-semibold">Applications</h3>
            <PipelineBoard positionId={id} />
          </section>

          <section className="card overflow-hidden">
            <div className="border-ink-200 flex items-center justify-between border-b px-5 py-4">
              <h3 className="text-ink-900 text-sm font-semibold">All candidates</h3>
              <Link to="/candidates" className="text-brand-700 text-sm font-medium hover:underline">
                Manage
              </Link>
            </div>
            {candidateList.length === 0 ? (
              <EmptyState
                icon={<UsersIcon className="h-8 w-8" />}
                title="No candidates"
                description="Add candidates from the Candidates page."
              />
            ) : (
              <ul className="divide-ink-100 divide-y">
                {candidateList.map((candidate) => (
                  <li key={candidate.id}>
                    <Link
                      to={`/candidates/${candidate.id}`}
                      className="hover:bg-ink-50 flex items-center justify-between gap-3 px-5 py-3 transition-colors"
                    >
                      <span className="text-ink-900 truncate text-sm">{candidate.full_name}</span>
                      <CandidateStatusBadge status={candidate.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </TabPanel>

        <TabPanel idPrefix="position-detail" value="agent" active={tab === 'agent'}>
          <section className="card overflow-hidden">
            <div className="border-ink-200 flex items-center justify-between border-b px-5 py-4">
              <div>
                <h3 className="text-ink-900 text-sm font-semibold">Agent configuration</h3>
                <p className="text-ink-500 mt-0.5 text-xs">
                  Configurations scoped to this position only. A workspace-wide default may also apply.
                </p>
              </div>
              <Link to="/agent-config" className="text-brand-700 text-sm font-medium hover:underline">
                Manage
              </Link>
            </div>

            {agentConfigs.loading && <LoadingBlock label="Loading agent configuration…" />}
            {agentConfigs.error && <ErrorAlert message={agentConfigs.error} className="m-5" />}

            {!agentConfigs.loading && !agentConfigs.error && ownConfigs.length === 0 && (
              <EmptyState
                icon={<RobotIcon className="h-10 w-10" />}
                title="No configuration scoped to this position"
                description="This position uses the workspace-wide default, if one exists. Create a position-specific configuration from Agent Configuration."
                action={
                  <Link to="/agent-config">
                    <Button variant="secondary">
                      <RobotIcon className="h-4 w-4" />
                      Go to Agent Configuration
                    </Button>
                  </Link>
                }
              />
            )}

            {ownConfigs.length > 0 && (
              <ul className="divide-ink-100 divide-y">
                {ownConfigs.map((config) => {
                  const persona = asPersona(config.config)
                  return (
                    <li key={config.id} className="px-5 py-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-ink-900 text-sm font-medium">{config.name}</p>
                          {persona && (
                            <p className="text-ink-500 mt-0.5 truncate text-xs">
                              {persona.agent_name} · {persona.language} · {persona.tone}
                            </p>
                          )}
                        </div>
                        <Badge tone={config.is_active ? 'success' : 'neutral'}>
                          {config.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </div>
                      {persona && (
                        <p className="text-ink-600 mt-2 line-clamp-2 text-xs italic">
                          “{persona.opening_script}”
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        </TabPanel>

        <TabPanel idPrefix="position-detail" value="screening" active={tab === 'screening'}>
          <ScreeningSetupPanel positionId={id} questionCount={questionList.length} />
        </TabPanel>
      </div>

      <Modal open={addOpen} title="Add question" onClose={() => setAddOpen(false)}>
        <QuestionForm
          initial={EMPTY_QUESTION}
          submitLabel="Add question"
          onSubmit={async (values) => {
            await addQuestion(id, {
              category: values.category as QuestionCategory,
              question: values.question,
              order: questionList.length,
              purpose: values.purpose.trim() === '' ? null : values.purpose,
              expected_topics: parseTopics(values.expectedTopics),
              difficulty: values.difficulty,
              follow_up_allowed: values.followUpAllowed,
            })
            setAddOpen(false)
            questions.reload()
          }}
        />
      </Modal>

      <SuggestQuestionsModal
        open={suggestOpen}
        positionId={id}
        existingQuestionCount={questionList.length}
        onClose={() => setSuggestOpen(false)}
        onAdded={() => questions.reload()}
      />

      <Modal open={editing !== null} title="Edit question" onClose={() => setEditing(null)}>
        {editing && (
          <QuestionForm
            initial={{
              category: editing.category,
              question: editing.question,
              purpose: editing.purpose ?? '',
              expectedTopics: editing.expected_topics.join(', '),
              difficulty: editing.difficulty,
              followUpAllowed: editing.follow_up_allowed,
            }}
            submitLabel="Save question"
            onSubmit={async (values) => {
              await updateQuestion(editing.id, {
                category: values.category as QuestionCategory,
                question: values.question,
                purpose: values.purpose.trim() === '' ? null : values.purpose,
                expected_topics: parseTopics(values.expectedTopics),
                difficulty: values.difficulty,
                follow_up_allowed: values.followUpAllowed,
              })
              setEditing(null)
              questions.reload()
            }}
          />
        )}
      </Modal>

      <Modal
        open={editPositionOpen}
        title="Edit position"
        onClose={() => setEditPositionOpen(false)}
      >
        <EditPositionForm
          position={current}
          onSaved={() => {
            setEditPositionOpen(false)
            position.reload()
          }}
        />
      </Modal>
    </>
  )
}
