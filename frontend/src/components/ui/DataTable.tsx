import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'

export interface DataTableColumn<T> {
  key: string
  header: ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
  render: (row: T) => ReactNode
}

/** A real <table>, styled consistently -- replaces the hand-rolled
 *  `<table className="w-full min-w-[720px] ...">` markup repeated across
 *  Positions/Candidates/Queues/QueueDetail/ScreeningResultPanel. Same data,
 *  same links and actions in each cell; only the markup is shared.
 *
 *  No sorting, filtering, selection or pagination -- none of that changes
 *  here, this only replaces how the existing rows/columns are drawn.
 *
 *  Deliberately no hardcoded min-width: the old tables forced horizontal
 *  scroll on any viewport narrower than 720px regardless of how few columns
 *  they had. Columns now size to content, with overflow-x-auto remaining as
 *  the fallback for genuinely dense tables on narrow screens. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  className,
}: {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T) => string | number
  /** Visually hidden, for screen readers -- what this table lists. */
  caption?: string
  className?: string
}) {
  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-surface-sunken text-ink-600 border-border-default border-b text-xs font-semibold tracking-wide uppercase">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  'px-5 py-3 font-semibold whitespace-nowrap',
                  column.align === 'right' && 'text-right',
                  column.align === 'center' && 'text-center',
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-border-subtle divide-y">
          {rows.map((row) => (
            <tr key={rowKey(row)} className="hover:bg-ink-50 transition-standard transition-colors">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn(
                    'px-5 py-3.5',
                    column.align === 'right' && 'text-right',
                    column.align === 'center' && 'text-center',
                    column.className,
                  )}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
