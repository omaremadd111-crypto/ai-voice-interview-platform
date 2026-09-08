import type { ReactNode } from 'react'

import type {
  CandidateStatus,
  PositionStatus,
  QueueItemStatus,
  QueueStatus,
  QuestionCategory,
  ScreeningOutcome,
} from '@/api/types'
import { cn } from '@/lib/cn'
import {
  CANDIDATE_STATUS_LABEL,
  QUESTION_CATEGORY_LABEL,
  QUEUE_ITEM_STATUS_LABEL,
  QUEUE_STATUS_LABEL,
} from '@/lib/labels'

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'

// Tokenized onto the same success/warning/danger/info ramps used app-wide
// (index.css), replacing the stock Tailwind emerald/amber/red/sky this used
// before -- same 5 tones, same visual weight, now part of one palette.
const TONES: Record<Tone, string> = {
  neutral: 'bg-ink-100 text-ink-700 ring-ink-200',
  brand: 'bg-brand-50 text-brand-700 ring-brand-200',
  success: 'bg-success-50 text-success-700 ring-success-200',
  warning: 'bg-warning-50 text-warning-800 ring-warning-200',
  danger: 'bg-danger-50 text-danger-700 ring-danger-200',
  info: 'bg-info-50 text-info-700 ring-info-200',
}

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

const POSITION_STATUS_TONE: Record<PositionStatus, Tone> = {
  draft: 'neutral',
  active: 'success',
  closed: 'warning',
}

export function PositionStatusBadge({ status }: { status: PositionStatus }) {
  return <Badge tone={POSITION_STATUS_TONE[status]}>{status}</Badge>
}

const CANDIDATE_STATUS_TONE: Record<CandidateStatus, Tone> = {
  new: 'info',
  applied: 'info',
  queued: 'neutral',
  invited: 'brand',
  screening_in_progress: 'brand',
  screened: 'success',
  withdrawn: 'neutral',
}

export function CandidateStatusBadge({ status }: { status: CandidateStatus }) {
  return <Badge tone={CANDIDATE_STATUS_TONE[status]}>{CANDIDATE_STATUS_LABEL[status]}</Badge>
}

const CATEGORY_TONE: Record<QuestionCategory, Tone> = {
  introduction: 'neutral',
  candidate_background: 'info',
  cv_project_validation: 'warning',
  technical: 'brand',
  problem_solving: 'success',
  behavioral: 'info',
  closing: 'neutral',
}

export function CategoryBadge({ category }: { category: QuestionCategory }) {
  return <Badge tone={CATEGORY_TONE[category]}>{QUESTION_CATEGORY_LABEL[category]}</Badge>
}

const QUEUE_STATUS_TONE: Record<QueueStatus, Tone> = {
  idle: 'neutral',
  running: 'success',
  paused: 'warning',
}

export function QueueStatusBadge({ status }: { status: QueueStatus }) {
  return <Badge tone={QUEUE_STATUS_TONE[status]}>{QUEUE_STATUS_LABEL[status]}</Badge>
}

// "Could not complete" and "No answer" are amber, not red: neither is a problem
// with the candidate, and neither should read as a verdict on them.
const QUEUE_ITEM_STATUS_TONE: Record<QueueItemStatus, Tone> = {
  awaiting_candidate: 'neutral',
  pending: 'neutral',
  claimed: 'info',
  in_progress: 'brand',
  completed: 'success',
  failed: 'warning',
  no_answer: 'warning',
  cancelled: 'neutral',
}

export function QueueItemStatusBadge({ status }: { status: QueueItemStatus }) {
  return <Badge tone={QUEUE_ITEM_STATUS_TONE[status]}>{QUEUE_ITEM_STATUS_LABEL[status]}</Badge>
}

const SCREENING_OUTCOME_TONE: Record<ScreeningOutcome, Tone> = {
  PASS: 'success',
  FAIL: 'warning',
  NEEDS_REVIEW: 'info',
}

export function ScreeningOutcomeBadge({ outcome }: { outcome: ScreeningOutcome }) {
  return <Badge tone={SCREENING_OUTCOME_TONE[outcome]}>{outcome}</Badge>
}
