import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/cn'
import { Spinner } from '@/components/ui/Feedback'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-600 text-white hover:bg-brand-700 disabled:hover:bg-brand-600 shadow-sm',
  secondary:
    'bg-surface-raised text-ink-700 border border-border-strong hover:bg-ink-50 disabled:hover:bg-surface-raised',
  ghost: 'bg-transparent text-ink-600 hover:bg-ink-100 disabled:hover:bg-transparent',
  danger: 'bg-danger-600 text-white hover:bg-danger-700 disabled:hover:bg-danger-600 shadow-sm',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-sm',
  md: 'h-10 px-4 text-sm',
}

// Spinner tint has to match each variant's foreground, since the spinner sits
// on primary/danger's colored fill as well as secondary/ghost's neutral text.
const SPINNER_TONE: Record<Variant, string> = {
  primary: 'border-white/40 border-t-white',
  danger: 'border-white/40 border-t-white',
  secondary: 'border-ink-300 border-t-ink-600',
  ghost: 'border-ink-300 border-t-ink-600',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  children: ReactNode
  /** Shows an inline spinner and disables the button. Callers that already
   *  pass their own <Spinner> + disabled (the existing pattern throughout the
   *  app) are unaffected -- this is purely additive. */
  loading?: boolean
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  children,
  loading = false,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium',
        'transition-standard transition-[background-color,border-color,box-shadow]',
        'disabled:cursor-not-allowed disabled:opacity-60',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <Spinner className={cn('h-4 w-4', SPINNER_TONE[variant])} />}
      {children}
    </button>
  )
}
