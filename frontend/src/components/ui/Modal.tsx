import { useEffect, useRef, type ReactNode } from 'react'

import { CloseIcon } from '@/components/ui/Icons'

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
}: {
  open: boolean
  title: string
  description?: string
  onClose: () => void
  children: ReactNode
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const previouslyFocusedRef = useRef<HTMLElement | null>(null)

  // Move focus into the dialog on open, and back to whatever triggered it on
  // close -- without this, a keyboard or screen-reader user opening the modal
  // stays focused on a now-hidden trigger, and loses their place entirely
  // when it closes.
  useEffect(() => {
    if (!open) return
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    return () => {
      previouslyFocusedRef.current?.focus?.()
    }
  }, [open])

  // Escape closes; Tab/Shift+Tab is trapped inside the dialog so keyboard
  // focus can never land on the page behind it while open.
  useEffect(() => {
    if (!open) return

    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      )
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) {
        event.preventDefault()
        return
      }

      const active = document.activeElement
      const activeIsInside = active instanceof Node && dialogRef.current.contains(active)

      if (event.shiftKey) {
        if (!activeIsInside || active === first) {
          event.preventDefault()
          last.focus()
        }
      } else if (!activeIsInside || active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-6">
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        className="bg-ink-950/40 fixed inset-0 cursor-default"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="card relative z-10 my-8 w-full max-w-xl p-6 shadow-lg outline-none"
      >
        <button
          type="button"
          aria-label="Close dialog"
          onClick={onClose}
          className="text-ink-400 hover:bg-ink-100 hover:text-ink-700 absolute top-4 right-4 rounded-lg p-1.5 transition-colors"
        >
          <CloseIcon className="h-4 w-4" />
        </button>
        <h2 className="text-ink-900 pr-8 text-lg font-semibold">{title}</h2>
        {description && <p className="text-ink-500 mt-1 pr-8 text-sm">{description}</p>}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  )
}
