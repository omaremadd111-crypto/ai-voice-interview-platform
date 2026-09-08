import { MAX_CV_UPLOAD_BYTES, SUPPORTED_CV_EXTENSIONS } from '@/api/candidates'

/**
 * Client-side checks shared by every place that accepts a CV file (the
 * candidate-detail panel and the add-candidate modal), so the two never drift.
 *
 * These are a fast-feedback convenience, not the authority: the backend runs the
 * same checks and remains the only thing that decides whether a file is
 * readable, since only it can actually parse the document.
 */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function hasSupportedCvExtension(name: string): boolean {
  const lower = name.toLowerCase()
  return SUPPORTED_CV_EXTENSIONS.some((extension) => lower.endsWith(extension))
}

/** Returns a human-readable problem with the file, or null when it looks usable. */
export function validateCvFile(file: File): string | null {
  if (!hasSupportedCvExtension(file.name)) {
    return `Unsupported file type. Choose one of: ${SUPPORTED_CV_EXTENSIONS.join(', ')}`
  }
  if (file.size === 0) {
    return 'That file is empty. Choose a file with content.'
  }
  if (file.size > MAX_CV_UPLOAD_BYTES) {
    return `That file is ${formatBytes(file.size)}, over the ${formatBytes(MAX_CV_UPLOAD_BYTES)} limit.`
  }
  return null
}

/** "Accepted: .pdf, .docx, .txt, .md · up to 5.0 MB" */
export const CV_ACCEPT_ATTRIBUTE = SUPPORTED_CV_EXTENSIONS.join(',')

export const CV_ACCEPT_HINT =
  `Accepted: ${SUPPORTED_CV_EXTENSIONS.join(', ')} · up to ${formatBytes(MAX_CV_UPLOAD_BYTES)}`
