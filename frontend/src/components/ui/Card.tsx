import type { HTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/cn'

/** The existing `.card` utility (index.css), as a component -- formalizes the
 *  raised-surface treatment already used ~40 places across the app so new
 *  code reaches for one thing instead of re-typing the className. */
export function Card({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('card', className)} {...props}>
      {children}
    </div>
  )
}

/** The bordered title-row header repeated at the top of several cards
 *  (Dashboard's "Positions" / "Recent candidates" panels, table wrappers). */
export function CardHeader({
  title,
  actions,
  className,
}: {
  title: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'border-border-default flex items-center justify-between gap-3 border-b px-5 py-4',
        className,
      )}
    >
      <h3 className="text-ink-900 text-sm font-semibold">{title}</h3>
      {actions}
    </div>
  )
}
