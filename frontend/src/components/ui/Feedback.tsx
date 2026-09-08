import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'
import { AlertIcon, RefreshIcon } from '@/components/ui/Icons'

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cn(
        'border-ink-300 border-t-brand-600 inline-block animate-spin rounded-full border-2',
        className ?? 'h-5 w-5',
      )}
    />
  )
}

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16">
      <Spinner />
      <span className="text-ink-500 text-sm">{label}</span>
    </div>
  )
}

/** A pulsing placeholder block. Sized entirely by className -- compose page
 *  layouts (a title line, a row of stat tiles, a table's worth of rows) by
 *  stacking Skeletons in the same shape the real content will take, so the
 *  page does not jump when data arrives. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn('bg-ink-200/70 animate-pulse rounded-md', className ?? 'h-4 w-full')}
    />
  )
}

export function ErrorAlert({ message, className }: { message: string; className?: string }) {
  return (
    <div
      role="alert"
      className={cn(
        'border-danger-200 bg-danger-50 text-danger-800 rounded-lg border px-4 py-3 text-sm',
        className,
      )}
    >
      {message}
    </div>
  )
}

/** Full-block failure state for a page/section whose data failed to load --
 *  distinct from ErrorAlert, which stays inline for form/action failures.
 *  `onRetry`, when passed, calls back into the SAME useAsync().reload() the
 *  page already has; no new data-fetching behavior, just a way to trigger the
 *  existing one without a manual browser refresh. */
export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn('flex flex-col items-center justify-center px-6 py-16 text-center', className)}
    >
      <div className="bg-danger-50 text-danger-600 mb-3 rounded-full p-2.5">
        <AlertIcon className="h-6 w-6" />
      </div>
      <h3 className="text-ink-900 text-base font-semibold">Something went wrong</h3>
      <p className="text-ink-500 mt-1 max-w-md text-sm">{message}</p>
      {onRetry && (
        // A plain button, not the Button primitive: Button itself renders a
        // Spinner from this file, so importing Button here would be circular.
        <button
          type="button"
          onClick={onRetry}
          className={cn(
            'border-border-strong text-ink-700 hover:bg-ink-50 mt-5 inline-flex h-8 items-center',
            'justify-center gap-2 rounded-lg border bg-surface-raised px-3 text-sm font-medium',
            'transition-standard transition-colors',
          )}
        >
          <RefreshIcon className="h-4 w-4" />
          Try again
        </button>
      )}
    </div>
  )
}

export function SuccessAlert({ message, className }: { message: string; className?: string }) {
  return (
    <div
      role="status"
      className={cn(
        'border-success-200 bg-success-50 text-success-800 rounded-lg border px-4 py-3 text-sm',
        className,
      )}
    >
      {message}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      {icon && <div className="text-ink-400 mb-3">{icon}</div>}
      <h3 className="text-ink-900 text-base font-semibold">{title}</h3>
      {description && <p className="text-ink-500 mt-1 max-w-md text-sm">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
