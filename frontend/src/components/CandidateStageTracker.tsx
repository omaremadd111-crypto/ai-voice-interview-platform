import { CheckIcon } from '@/components/ui/Icons'
import { cn } from '@/lib/cn'

export type StageStatus = 'complete' | 'current' | 'upcoming'

export interface Stage {
  key: string
  label: string
  status: StageStatus
}

/**
 * A horizontal progress stepper for where a candidate stands in the
 * screening journey. Purely presentational -- callers decide each step's
 * status from data they already loaded; this only draws the result.
 *
 * `current` marks the next step nothing has satisfied yet, not necessarily
 * something blocking -- a withdrawn candidate, for instance, simply stops
 * advancing wherever they were, and the step after that stays "current"
 * without implying the candidate will reach it.
 */
export function CandidateStageTracker({ stages }: { stages: Stage[] }) {
  return (
    <ol aria-label="Candidate stage" className="flex items-start overflow-x-auto pb-0.5">
      {stages.map((stage, index) => (
        <li
          key={stage.key}
          className="flex min-w-[76px] flex-1 shrink-0 flex-col items-center last:min-w-fit last:flex-none"
        >
          <div className="flex w-full items-center">
            <span
              aria-hidden="true"
              className={cn(
                'h-px flex-1',
                index === 0
                  ? 'invisible'
                  : stages[index - 1]?.status === 'complete'
                    ? 'bg-success-300'
                    : 'bg-border-default',
              )}
            />
            <span
              className={cn(
                'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold',
                stage.status === 'complete' && 'bg-success-500 text-white',
                stage.status === 'current' && 'border-brand-600 text-brand-700 border-2 bg-white',
                stage.status === 'upcoming' && 'bg-ink-100 text-ink-400',
              )}
            >
              {stage.status === 'complete' ? <CheckIcon className="h-3.5 w-3.5" /> : index + 1}
            </span>
            <span
              aria-hidden="true"
              className={cn(
                'h-px flex-1',
                index === stages.length - 1
                  ? 'invisible'
                  : stage.status === 'complete'
                    ? 'bg-success-300'
                    : 'bg-border-default',
              )}
            />
          </div>
          <span
            className={cn(
              'mt-1.5 px-1 text-center text-[11px] font-medium',
              stage.status === 'upcoming' ? 'text-ink-400' : 'text-ink-700',
            )}
          >
            {stage.label}
            {stage.status === 'current' && <span className="sr-only"> (current stage)</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}
