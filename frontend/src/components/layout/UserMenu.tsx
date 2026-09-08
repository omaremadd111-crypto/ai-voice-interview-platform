import { useEffect, useRef, useState } from 'react'

import { Avatar } from '@/components/ui/Avatar'
import { ChevronDownIcon, LogoutIcon } from '@/components/ui/Icons'

/** Replaces the bare avatar + a second, mobile-only sign-out icon button that
 *  used to sit in the topbar (the primary sign-out already lives in the
 *  sidebar footer -- two controls for one action, in two different places).
 *  One menu, one place, works at every width. */
export function UserMenu({ email, onLogout }: { email: string | null; onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return

    function handlePointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKey)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="hover:bg-ink-100 transition-standard flex items-center gap-2 rounded-full py-1 pr-2 pl-1 transition-colors"
      >
        <Avatar name={email ?? '?'} size="sm" />
        <ChevronDownIcon
          className={`text-ink-400 h-4 w-4 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="border-border-default bg-surface-raised shadow-lg animate-menu-in absolute top-full right-0 z-40 mt-2 w-60 origin-top-right rounded-xl border py-1.5"
        >
          <div className="border-border-subtle border-b px-3.5 py-2.5">
            <p className="text-ink-400 text-[11px] font-medium tracking-wide uppercase">
              Signed in as
            </p>
            <p className="text-ink-900 mt-0.5 truncate text-sm font-medium">{email}</p>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onLogout()
            }}
            className="text-ink-600 hover:bg-ink-50 hover:text-ink-900 flex w-full items-center gap-2.5 px-3.5 py-2 text-sm font-medium transition-colors"
          >
            <LogoutIcon className="h-4 w-4" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
