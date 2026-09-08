import { useEffect, useState, type ReactNode } from 'react'

import {
  approveInterviewPlan,
  generateInterviewPlan,
  regeneratePlanQuestion,
  saveInterviewPlan,
} from '@/api/interviewPlans'
import type {
  CandidateInterviewPlan,
  PlanQuestion,
  PlanQuestionSource,
  QuestionCategory,
} from '@/api/types'
import { Badge, CategoryBadge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field'
import { EmptyState, ErrorAlert, Spinner, SuccessAlert } from '@/components/ui/Feedback'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronLeftIcon,
  DocumentIcon,
  PlusIcon,
  SparkIcon,
  TrashIcon,
} from '@/components/ui/Icons'
import { QUESTION_CATEGORY_LABEL } from '@/lib/labels'
import { toMessage } from '@/lib/useAsync'

const CATEGORIES = Object.keys(QUESTION_CATEGORY_LABEL) as QuestionCategory[]
const DIFFICULTIES = ['easy', 'medium', 'hard'] as const

const SOURCE_LABEL: Record<PlanQuestionSource, string> = {
  bank: 'Role baseline',
  generated: 'AI-generated',
  manual: 'Added by you',
}

const SOURCE_TONE = {
  bank: 'neutral',
  generated: 'brand',
  manual: 'info',
} as const

function blankQuestion(): PlanQuestion {
  return {
    order: 0,
    // Blank on purpose: category is required and must be an explicit choice,
    // never a silent default that would misdirect how the answer is evaluated.
    category: '' as QuestionCategory,
    question: '',
    purpose: null,
    expected_topics: [],
    difficulty: 'medium',
    follow_up_allowed: true,
    source: 'manual',
  }
}

/** One plan question rendered as a static record: no inputs, no controls.
 *  Used only in read-only mode, where the plan is history rather than a draft. */
function ReadOnlyQuestion({ question, index }: { question: PlanQuestion; index: number }) {
  return (
    <li className="flex gap-3 py-4 first:pt-0 last:pb-0">
      <span className="bg-ink-100 text-ink-600 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold tabular-nums">
        {index + 1}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-ink-900 text-sm">{question.question}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge tone={SOURCE_TONE[question.source]}>{SOURCE_LABEL[question.source]}</Badge>
          {question.category && <CategoryBadge category={question.category} />}
          <span className="text-ink-500 text-xs capitalize">{question.difficulty}</span>
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
    </li>
  )
}

/**
 * Edit a candidate's interview plan: change wording, add, delete, reorder,
 * regenerate a single question or the whole plan, save, and approve.
 *
 * Edits are local until Save, so HR can rearrange freely and back out. Approval
 * is a separate, explicit step — and any later edit returns the plan to draft,
 * because the recruiter approved a specific set of questions.
 *
 * Once the interview itself has started, `readOnly` turns this into an audit
 * view: the questions stay fully legible, but nothing that could rewrite the
 * record of what the candidate was actually screened against is rendered at
 * all — not disabled, absent. The caller owns that decision, because only it
 * knows the candidate's interview status.
 */
export function InterviewPlanEditor({
  candidateId,
  plan,
  onPlanChange,
  headerActions,
  readOnly = false,
  readOnlyReason,
}: {
  candidateId: number
  plan: CandidateInterviewPlan | null
  onPlanChange: (plan: CandidateInterviewPlan) => void
  headerActions?: ReactNode
  readOnly?: boolean
  readOnlyReason?: string
}) {
  const [draft, setDraft] = useState<PlanQuestion[] | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const questions = draft ?? plan?.questions ?? []
  const dirty = draft !== null

  useEffect(() => {
    setActiveIndex((current) => Math.min(current, Math.max(questions.length - 1, 0)))
  }, [questions.length])

  async function run(label: string, action: () => Promise<CandidateInterviewPlan>, done?: string) {
    setError(null)
    setSuccess(null)
    setBusy(label)
    try {
      const updated = await action()
      onPlanChange(updated)
      setDraft(null)
      if (done) setSuccess(done)
    } catch (caught) {
      setError(toMessage(caught, 'Something went wrong.'))
    } finally {
      setBusy(null)
    }
  }

  function edit(index: number, changes: Partial<PlanQuestion>) {
    setSuccess(null)
    setDraft(questions.map((q, i) => (i === index ? { ...q, ...changes } : q)))
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= questions.length) return
    const next = [...questions]
    const moved = next[index]!
    next[index] = next[target]!
    next[target] = moved
    setSuccess(null)
    setDraft(next)
    setActiveIndex(target)
  }

  function remove(index: number) {
    setSuccess(null)
    setDraft(questions.filter((_, i) => i !== index))
    setActiveIndex(Math.min(index, Math.max(questions.length - 2, 0)))
  }

  function add() {
    setSuccess(null)
    setDraft([...questions, blankQuestion()])
    setActiveIndex(questions.length)
  }

  async function save() {
    const invalid = questions.findIndex((q) => !q.category || q.question.trim() === '')
    if (invalid !== -1) {
      setActiveIndex(invalid)
      setError(
        `Question ${invalid + 1} needs both a category and question text before the plan can be saved.`,
      )
      return
    }
    await run(
      'save',
      () =>
        saveInterviewPlan(
          candidateId,
          questions.map(({ order: _order, ...rest }) => rest),
        ),
      'Plan saved. Approve it when you are ready to interview.',
    )
  }

  if (plan === null) {
    // An interview that has already run with no plan on record is an
    // inconsistent state, not an invitation to generate one now — offering
    // "Generate interview plan" here would write a plan that no interview
    // ever used.
    if (readOnly) {
      return (
        <section className="card" data-testid="interview-plan-readonly-empty">
          <EmptyState
            icon={<DocumentIcon className="h-10 w-10" />}
            title="No interview plan on record"
            description={
              readOnlyReason ??
              'This interview has already run, so a plan can no longer be generated here.'
            }
          />
        </section>
      )
    }
    return (
      <section className="card">
        {error && <ErrorAlert message={error} className="m-5 mb-0" />}
        <EmptyState
          icon={<DocumentIcon className="h-10 w-10" />}
          title="No interview plan yet"
          description="Generate a plan from this role's question bank and the candidate's CV. You review and approve it before any interview runs."
          action={
            <Button
              onClick={() =>
                run('generate', () => generateInterviewPlan(candidateId), 'Plan generated.')
              }
              disabled={busy !== null}
            >
              {busy === 'generate' ? (
                <Spinner className="h-4 w-4 border-white/40 border-t-white" />
              ) : (
                <SparkIcon className="h-4 w-4" />
              )}
              {busy === 'generate' ? 'Generating…' : 'Generate interview plan'}
            </Button>
          }
        />
      </section>
    )
  }

  const approved = plan.status === 'approved' && !dirty
  const activeQuestion = questions[activeIndex]

  if (readOnly) {
    return (
      <section className="card" data-testid="interview-plan-readonly">
        <div className="border-ink-200 flex flex-col gap-3 rounded-t-xl border-b px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-ink-900 text-sm font-semibold">Interview plan</h3>
              {plan.status === 'approved' ? (
                <Badge tone="success">Approved</Badge>
              ) : (
                <Badge tone="neutral">Draft</Badge>
              )}
              <Badge tone="neutral">Read-only</Badge>
            </div>
            <p className="text-ink-500 mt-0.5 text-xs">
              The questions this candidate was screened against.
            </p>
          </div>
          {headerActions && (
            <div className="flex shrink-0 flex-wrap items-center gap-2">{headerActions}</div>
          )}
        </div>

        <div className="space-y-4 p-5">
          <p className="border-ink-200 bg-ink-50 text-ink-700 rounded-lg border px-4 py-3 text-sm">
            {readOnlyReason ??
              'The interview has already started, so this plan is kept as a record and can no longer be edited.'}
          </p>

          {questions.length === 0 ? (
            <div className="border-ink-200 rounded-xl border border-dashed px-5 py-10 text-center">
              <p className="text-ink-700 text-sm font-medium">This plan has no questions.</p>
            </div>
          ) : (
            <ol className="divide-ink-100 divide-y">
              {questions.map((question, index) => (
                <ReadOnlyQuestion key={index} question={question} index={index} />
              ))}
            </ol>
          )}
        </div>
      </section>
    )
  }

  return (
    <section className="card">
      <div
        className="border-ink-200 sticky top-16 z-20 flex flex-col gap-3 rounded-t-xl border-b bg-white/95 px-5 py-4 shadow-sm backdrop-blur sm:flex-row sm:items-center sm:justify-between"
        data-testid="interview-plan-toolbar"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-ink-900 text-sm font-semibold">Interview plan</h3>
            {approved ? (
              <Badge tone="success">Approved</Badge>
            ) : (
              <Badge tone="warning">{dirty ? 'Unsaved changes' : 'Draft — not yet approved'}</Badge>
            )}
          </div>
          <p className="text-ink-500 mt-0.5 text-xs">
            The role's baseline questions plus questions written for this candidate.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {headerActions}
          <Button size="sm" onClick={save} disabled={busy !== null || !dirty}>
            {busy === 'save' && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
            Save plan
          </Button>
          <Button
            size="sm"
            onClick={() =>
              run(
                'approve',
                () => approveInterviewPlan(candidateId),
                'Plan approved. It will be used for this interview.',
              )
            }
            disabled={busy !== null || dirty || approved || questions.length === 0}
            title={dirty ? 'Save your changes before approving' : undefined}
          >
            {busy === 'approve' && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
            {approved ? 'Approved' : 'Approve plan'}
          </Button>
        </div>
      </div>

      <div className="space-y-4 p-5">
        {error && <ErrorAlert message={error} />}
        {success && !error && <SuccessAlert message={success} />}

        {approved && (
          <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            This plan is approved and will be used for the interview. Editing it returns it to
            draft for re-approval.
          </p>
        )}

        {questions.length > 0 && (
          <nav
            aria-label="Interview plan questions"
            className="border-ink-200 bg-ink-50 rounded-xl border p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                aria-label="Previous question"
                disabled={activeIndex === 0}
                onClick={() => setActiveIndex((current) => Math.max(current - 1, 0))}
              >
                <ChevronLeftIcon className="h-4 w-4" />
                <span className="hidden sm:inline">Previous</span>
              </Button>
              <p className="text-ink-700 text-sm font-semibold tabular-nums" aria-live="polite">
                Question {activeIndex + 1} of {questions.length}
              </p>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                aria-label="Next question"
                disabled={activeIndex === questions.length - 1}
                onClick={() =>
                  setActiveIndex((current) => Math.min(current + 1, questions.length - 1))
                }
              >
                <span className="hidden sm:inline">Next</span>
                <ChevronLeftIcon className="h-4 w-4 rotate-180" />
              </Button>
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {questions.map((question, index) => {
                const active = index === activeIndex
                return (
                  <button
                    key={index}
                    type="button"
                    aria-label={`Go to question ${index + 1}`}
                    aria-current={active ? 'step' : undefined}
                    title={question.question || `Question ${index + 1}`}
                    onClick={() => setActiveIndex(index)}
                    className={`flex h-9 min-w-9 shrink-0 items-center justify-center rounded-lg border text-sm font-semibold tabular-nums transition-colors ${
                      active
                        ? 'border-brand-600 bg-brand-600 text-white shadow-sm'
                        : 'border-ink-300 bg-white text-ink-600 hover:border-brand-300 hover:text-brand-700'
                    }`}
                  >
                    {index + 1}
                  </button>
                )
              })}
            </div>
          </nav>
        )}

        {activeQuestion ? (
          <div className="border-brand-200 ring-brand-100 rounded-xl border bg-white p-4 shadow-sm ring-2 sm:p-5">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="bg-brand-100 text-brand-700 flex h-7 w-7 items-center justify-center rounded-md text-xs font-semibold tabular-nums">
                    {activeIndex + 1}
                  </span>
                  <Badge tone={SOURCE_TONE[activeQuestion.source]}>
                    {SOURCE_LABEL[activeQuestion.source]}
                  </Badge>
                  {activeQuestion.category && <CategoryBadge category={activeQuestion.category} />}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    aria-label={`Move question ${activeIndex + 1} up`}
                    disabled={activeIndex === 0 || busy !== null}
                    onClick={() => move(activeIndex, -1)}
                    className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 rounded-md p-1.5 disabled:opacity-30"
                  >
                    <ArrowUpIcon className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Move question ${activeIndex + 1} down`}
                    disabled={activeIndex === questions.length - 1 || busy !== null}
                    onClick={() => move(activeIndex, 1)}
                    className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 rounded-md p-1.5 disabled:opacity-30"
                  >
                    <ArrowDownIcon className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Regenerate question ${activeIndex + 1}`}
                    title="Regenerate this question"
                    disabled={busy !== null || dirty}
                    onClick={() =>
                      run(
                        `regen-${activeIndex}`,
                        () => regeneratePlanQuestion(candidateId, activeIndex),
                        'Question regenerated.',
                      )
                    }
                    className="text-ink-400 hover:bg-brand-50 hover:text-brand-700 rounded-md p-1.5 disabled:opacity-30"
                  >
                    {busy === `regen-${activeIndex}` ? (
                      <Spinner className="h-4 w-4" />
                    ) : (
                      <SparkIcon className="h-4 w-4" />
                    )}
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete question ${activeIndex + 1}`}
                    disabled={busy !== null}
                    onClick={() => remove(activeIndex)}
                    className="text-ink-400 rounded-md p-1.5 hover:bg-red-50 hover:text-red-600 disabled:opacity-30"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <TextAreaField
                label={`Question ${activeIndex + 1}`}
                value={activeQuestion.question}
                onChange={(e) => edit(activeIndex, { question: e.target.value })}
                className="min-h-20"
              />
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <SelectField
                  label="Category"
                  required
                  value={activeQuestion.category}
                  onChange={(e) =>
                    edit(activeIndex, { category: e.target.value as QuestionCategory })
                  }
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
                  value={activeQuestion.difficulty}
                  onChange={(e) => edit(activeIndex, { difficulty: e.target.value })}
                >
                  {DIFFICULTIES.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </SelectField>
              </div>
              <div className="mt-3">
                <TextField
                  label="Purpose"
                  value={activeQuestion.purpose ?? ''}
                  onChange={(e) => edit(activeIndex, { purpose: e.target.value || null })}
                  hint="Why this question is being asked. Never shown to the candidate."
                />
              </div>
          </div>
        ) : (
          <div className="border-ink-200 rounded-xl border border-dashed px-5 py-10 text-center">
            <p className="text-ink-700 text-sm font-medium">This plan has no questions.</p>
            <p className="text-ink-500 mt-1 text-xs">Add a question to continue editing.</p>
          </div>
        )}

        <div className="border-ink-200 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={add} disabled={busy !== null}>
              <PlusIcon className="h-4 w-4" />
              Add question
            </Button>
            <Button
              variant="secondary"
              disabled={busy !== null}
              onClick={() =>
                run('generate', () => generateInterviewPlan(candidateId), 'Plan regenerated.')
              }
            >
              {busy === 'generate' ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <SparkIcon className="h-4 w-4" />
              )}
              Regenerate whole plan
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {dirty && (
              <Button variant="ghost" onClick={() => setDraft(null)} disabled={busy !== null}>
                Discard changes
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
