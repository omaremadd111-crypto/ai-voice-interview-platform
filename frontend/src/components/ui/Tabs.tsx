import { useRef, type KeyboardEvent, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

export interface TabItem {
  value: string
  label: string
}

/** Accessible tabs: roving tabindex, arrow/Home/End key navigation, proper
 *  tablist/tab/tabpanel roles. `idPrefix` namespaces the generated ids so a
 *  page using several Tabs instances never collides, and so a TabPanel knows
 *  which tab labels it without either component having to guess the other's
 *  internally-generated id. Not currently wired into any page -- built ready
 *  for the page-level information-architecture work planned for a later
 *  phase, not forced onto a page that has no tab-shaped UI today. */
export function Tabs({
  idPrefix,
  items,
  value,
  onChange,
  className,
}: {
  idPrefix: string
  items: TabItem[]
  value: string
  onChange: (value: string) => void
  className?: string
}) {
  const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % items.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + items.length) % items.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = items.length - 1
    if (nextIndex === null) return

    event.preventDefault()
    const next = items[nextIndex]
    if (!next) return
    onChange(next.value)
    buttonRefs.current[next.value]?.focus()
  }

  return (
    <div role="tablist" aria-label="Sections" className={cn('border-border-default flex gap-1 border-b', className)}>
      {items.map((item, index) => {
        const selected = item.value === value
        return (
          <button
            key={item.value}
            ref={(el) => {
              buttonRefs.current[item.value] = el
            }}
            role="tab"
            type="button"
            id={`${idPrefix}-tab-${item.value}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${item.value}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={cn(
              '-mb-px border-b-2 px-4 py-2.5 text-sm font-medium',
              'transition-standard transition-colors',
              selected
                ? 'border-brand-600 text-brand-700'
                : 'text-ink-500 hover:text-ink-800 border-transparent',
            )}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  idPrefix,
  value,
  active,
  className,
  children,
}: {
  idPrefix: string
  value: string
  active: boolean
  className?: string
  children: ReactNode
}) {
  if (!active) return null
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${value}`}
      aria-labelledby={`${idPrefix}-tab-${value}`}
      tabIndex={0}
      className={className}
    >
      {children}
    </div>
  )
}
