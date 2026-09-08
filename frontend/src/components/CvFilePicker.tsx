import { useId, useRef, useState, type DragEvent } from 'react'

import { CloseIcon, DocumentIcon, UploadIcon } from '@/components/ui/Icons'
import { cn } from '@/lib/cn'
import { CV_ACCEPT_ATTRIBUTE, CV_ACCEPT_HINT, formatBytes, validateCvFile } from '@/lib/cvFile'

/**
 * Choose-a-CV-file control: a bordered drag-and-drop zone with a native file
 * picker as the keyboard/click fallback. Selection only -- it never uploads;
 * the owner still decides what counts as "selected" via `onSelect`, which
 * receives the raw picked/dropped file exactly as before regardless of which
 * input path was used. The inline error here is a local, immediate echo of
 * that same shared `validateCvFile` check, purely for feedback -- it never
 * blocks or overrides what the owner does with `onSelect`.
 */
export function CvFilePicker({
  label = 'CV file',
  file,
  onSelect,
  disabled = false,
}: {
  label?: string
  file: File | null
  onSelect: (file: File | null) => void
  disabled?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const inputId = useId()
  const hintId = useId()
  // Dragging over a zone with nested elements fires dragenter/dragleave on
  // every child boundary crossed, not just the zone's own edge. A counter
  // (rather than a plain boolean) is the standard fix -- only treat the drag
  // as having left once every nested enter has been balanced by a leave.
  const dragCounter = useRef(0)
  const [dragActive, setDragActive] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  function pick(candidate: File | null) {
    setLocalError(candidate ? validateCvFile(candidate) : null)
    onSelect(candidate)
  }

  function openPicker() {
    if (!disabled) inputRef.current?.click()
  }

  function isFileDrag(event: DragEvent<HTMLDivElement>): boolean {
    return event.dataTransfer.types.includes('Files')
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (disabled || !isFileDrag(event)) return
    event.preventDefault()
    dragCounter.current += 1
    setDragActive(true)
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled || !isFileDrag(event)) return
    // A drop only fires if dragover is prevented -- browsers reject it otherwise.
    event.preventDefault()
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (disabled || !isFileDrag(event)) return
    dragCounter.current = Math.max(0, dragCounter.current - 1)
    if (dragCounter.current === 0) setDragActive(false)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (disabled || !isFileDrag(event)) return
    event.preventDefault()
    dragCounter.current = 0
    setDragActive(false)
    const dropped = event.dataTransfer.files?.[0] ?? null
    if (dropped) pick(dropped)
  }

  return (
    <div className="space-y-1.5">
      <span className="text-ink-700 block text-sm font-medium">{label}</span>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={CV_ACCEPT_ATTRIBUTE}
        disabled={disabled}
        // Reachable only via the visible drop zone's click/Enter, never by
        // tabbing to it directly -- otherwise a sighted keyboard user's first
        // Tab stop here would be an invisible control instead of the zone.
        tabIndex={-1}
        className="sr-only"
        aria-label="Choose a CV file"
        onChange={(event) => {
          const chosen = event.target.files?.[0] ?? null
          // Clear the input so re-picking the same file still fires onChange.
          event.target.value = ''
          pick(chosen)
        }}
      />

      <div onDragEnter={handleDragEnter} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
        {file === null ? (
          <button
            type="button"
            disabled={disabled}
            onClick={openPicker}
            aria-label="Upload CV file"
            aria-describedby={hintId}
            aria-invalid={localError ? true : undefined}
            className={cn(
              'flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center',
              'transition-standard transition-colors disabled:cursor-not-allowed disabled:opacity-60',
              dragActive
                ? 'border-brand-500 bg-brand-50'
                : localError
                  ? 'border-danger-300 bg-danger-50/40 hover:bg-danger-50/60'
                  : 'border-border-strong bg-surface-sunken hover:border-brand-400 hover:bg-brand-50/40',
            )}
          >
            <span
              className={cn(
                'flex h-10 w-10 items-center justify-center rounded-full',
                dragActive ? 'bg-brand-100 text-brand-700' : 'bg-brand-50 text-brand-600',
              )}
            >
              <UploadIcon className="h-5 w-5" />
            </span>
            <span className="text-ink-800 text-sm font-medium">
              {dragActive ? 'Drop your CV here' : 'Drag & drop your CV here'}
            </span>
            {!dragActive && (
              <span className="text-ink-500 text-xs">
                or <span className="text-brand-700 font-medium">choose a file</span>
              </span>
            )}
          </button>
        ) : (
          <div
            className={cn(
              'flex items-center gap-2 rounded-lg border px-3 py-2',
              'transition-standard transition-colors',
              dragActive
                ? 'border-brand-400 bg-brand-50'
                : 'border-border-default bg-surface-sunken',
            )}
          >
            <DocumentIcon className="text-ink-400 h-4 w-4 shrink-0" />
            <span className="text-ink-800 truncate text-sm">{file.name}</span>
            <span className="text-ink-500 shrink-0 text-xs tabular-nums">
              {formatBytes(file.size)}
            </span>
            <div className="ml-auto flex shrink-0 items-center gap-1">
              <button
                type="button"
                disabled={disabled}
                onClick={openPicker}
                className="text-ink-500 hover:text-ink-800 rounded px-1.5 py-0.5 text-xs font-medium disabled:opacity-50"
              >
                Replace
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => pick(null)}
                aria-label="Remove selected file"
                className="text-ink-400 hover:text-ink-800 shrink-0 rounded p-0.5 disabled:opacity-50"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      <p
        id={hintId}
        role={localError ? 'alert' : undefined}
        className={cn('text-xs', localError ? 'text-danger-600 font-medium' : 'text-ink-500')}
      >
        {localError ?? CV_ACCEPT_HINT}
      </p>
    </div>
  )
}
