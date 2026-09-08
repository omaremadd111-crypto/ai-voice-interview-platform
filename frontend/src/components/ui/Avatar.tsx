import { cn } from '@/lib/cn'

// Varied, deterministic tones (not one repeated blue) so a column of avatars
// gives scanning anchor points instead of reading as one flat color block.
const TONES = [
  'bg-brand-100 text-brand-700',
  'bg-info-100 text-info-700',
  'bg-success-100 text-success-700',
  'bg-warning-100 text-warning-800',
  'bg-danger-100 text-danger-700',
]

function toneFor(seed: string): string {
  let hash = 0
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0
  }
  return TONES[hash % TONES.length]!
}

function initialsFor(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0]![0]
  const last = words.length > 1 ? words[words.length - 1]![0] : ''
  return `${first}${last}`.toUpperCase()
}

/** An initials circle, toned deterministically from `name` -- the same person
 *  always gets the same color, and different rows in the same table get
 *  visibly different colors, which is what makes a column of these usable as
 *  a scanning aid rather than decoration. Purely presentational: the name
 *  text next to it already carries the accessible content, so this is
 *  aria-hidden. */
export function Avatar({
  name,
  size = 'md',
  className,
}: {
  name: string
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold',
        size === 'sm' ? 'h-7 w-7 text-[11px]' : 'h-9 w-9 text-xs',
        toneFor(name),
        className,
      )}
    >
      {initialsFor(name)}
    </span>
  )
}
