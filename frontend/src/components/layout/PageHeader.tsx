import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { ChevronLeftIcon } from '@/components/ui/Icons'

export function PageHeader({
  title,
  description,
  actions,
  backTo,
  backLabel,
}: {
  title: string
  description?: string
  actions?: ReactNode
  backTo?: string
  backLabel?: string
}) {
  return (
    <div className="mb-6">
      {backTo && (
        <Link
          to={backTo}
          className="text-ink-500 hover:text-ink-800 mb-3 inline-flex items-center gap-1 text-sm font-medium"
        >
          <ChevronLeftIcon className="h-4 w-4" />
          {backLabel ?? 'Back'}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {/* tracking comes from the text-2xl token itself (index.css) now,
              not a separate utility -- keeps the letter-spacing value in one
              place instead of two competing declarations. */}
          <h2 className="text-ink-900 text-2xl font-semibold">{title}</h2>
          {description && <p className="text-ink-500 mt-1 text-sm">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  )
}
