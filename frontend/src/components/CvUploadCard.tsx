import { useRef, useState, type ChangeEvent } from 'react'

import { updateCandidate, uploadCandidateCv } from '@/api/candidates'
import type { Candidate } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { TextAreaField } from '@/components/ui/Field'
import { ErrorAlert, Spinner, SuccessAlert } from '@/components/ui/Feedback'
import { DocumentIcon, TrashIcon, UploadIcon } from '@/components/ui/Icons'
import { CV_ACCEPT_ATTRIBUTE, CV_ACCEPT_HINT, validateCvFile } from '@/lib/cvFile'
import { toMessage } from '@/lib/useAsync'

/**
 * CV panel for a candidate: upload a real file (parsed server-side by the
 * existing DocumentParser) or fall back to pasting text. Both paths write to the
 * same stored CV text, which Prepare Interview then uses alongside the position's
 * job description.
 *
 * `readOnly` is set once the candidate's interview has run: the CV stays fully
 * readable as part of the audit trail, but replacing or clearing it would
 * silently change the record of what the completed screening was actually based
 * on, so those controls are withdrawn along with the forward-looking copy.
 */
export function CvUploadCard({
  candidate,
  onUpdated,
  readOnly = false,
}: {
  candidate: Candidate
  onUpdated: (updated: Candidate) => void
  readOnly?: boolean
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const [pasteOpen, setPasteOpen] = useState(false)
  const [pastedText, setPastedText] = useState(candidate.cv_text ?? '')
  const [savingText, setSavingText] = useState(false)

  const hasCv = candidate.cv_text !== null && candidate.cv_text !== ''

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Allow re-selecting the same file after an error.
    event.target.value = ''
    if (!file) return

    setError(null)
    setSuccess(null)
    setSelectedName(file.name)

    const problem = validateCvFile(file)
    if (problem !== null) {
      setError(problem)
      return
    }

    setUploading(true)
    try {
      const updated = await uploadCandidateCv(candidate.id, file)
      onUpdated(updated)
      setPastedText(updated.cv_text ?? '')
      setSuccess(`Uploaded and parsed “${file.name}”.`)
    } catch (caught) {
      setError(toMessage(caught, 'Could not upload the CV.'))
    } finally {
      setUploading(false)
    }
  }

  async function handleSaveText() {
    setError(null)
    setSuccess(null)
    setSavingText(true)
    try {
      const updated = await updateCandidate(candidate.id, {
        cv_text: pastedText.trim() === '' ? null : pastedText,
        cv_filename: pastedText.trim() === '' ? null : 'pasted-text',
      })
      onUpdated(updated)
      setPasteOpen(false)
      setSuccess(pastedText.trim() === '' ? 'CV cleared.' : 'CV text saved.')
    } catch (caught) {
      setError(toMessage(caught, 'Could not save the CV text.'))
    } finally {
      setSavingText(false)
    }
  }

  async function handleRemove() {
    setError(null)
    setSuccess(null)
    try {
      const updated = await updateCandidate(candidate.id, { cv_text: null, cv_filename: null })
      onUpdated(updated)
      setPastedText('')
      setSelectedName(null)
      setSuccess('CV removed.')
    } catch (caught) {
      setError(toMessage(caught, 'Could not remove the CV.'))
    }
  }

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-ink-900 text-sm font-semibold">Candidate CV</h3>
          <p className="text-ink-500 mt-0.5 text-xs">
            {readOnly
              ? 'The CV this candidate was screened against.'
              : 'Used with the job description to generate candidate-specific questions.'}
          </p>
        </div>
        {hasCv ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200 ring-inset">
            <DocumentIcon className="h-3.5 w-3.5" />
            Uploaded
          </span>
        ) : (
          <span className="bg-ink-100 text-ink-600 ring-ink-200 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset">
            No CV
          </span>
        )}
      </div>

      {error && <ErrorAlert message={error} className="mt-4" />}
      {success && !error && <SuccessAlert message={success} className="mt-4" />}

      <div className="mt-4 space-y-3">
        {!readOnly && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept={CV_ACCEPT_ATTRIBUTE}
              onChange={handleFileChange}
              className="sr-only"
              aria-label="Choose a CV file to upload"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant={hasCv ? 'secondary' : 'primary'}
                disabled={uploading}
                onClick={() => fileInputRef.current?.click()}
              >
                {uploading ? <Spinner className="h-4 w-4" /> : <UploadIcon className="h-4 w-4" />}
                {uploading ? 'Parsing…' : hasCv ? 'Replace CV' : 'Upload CV'}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setPasteOpen((open) => !open)}>
                {pasteOpen ? 'Hide text entry' : 'Paste CV text instead'}
              </Button>
              {hasCv && (
                <Button type="button" variant="ghost" onClick={handleRemove} aria-label="Remove CV">
                  <TrashIcon className="h-4 w-4" />
                  Remove
                </Button>
              )}
            </div>

            <p className="text-ink-500 text-xs">
              {CV_ACCEPT_HINT}
              {selectedName && !uploading && (
                <>
                  {' '}
                  · Selected: <span className="text-ink-700 font-medium">{selectedName}</span>
                </>
              )}
            </p>
          </>
        )}

        {candidate.cv_filename && (
          <div className="border-ink-200 bg-ink-50 flex items-center gap-2 rounded-lg border px-3 py-2">
            <DocumentIcon className="text-ink-400 h-4 w-4 shrink-0" />
            <span className="text-ink-700 truncate text-sm">{candidate.cv_filename}</span>
            {candidate.cv_text && (
              <span className="text-ink-500 ml-auto shrink-0 text-xs tabular-nums">
                {candidate.cv_text.length.toLocaleString()} chars
              </span>
            )}
          </div>
        )}

        {!readOnly && pasteOpen && (
          <div className="border-ink-200 space-y-3 border-t pt-3">
            <TextAreaField
              label="CV text"
              value={pastedText}
              onChange={(e) => setPastedText(e.target.value)}
              className="min-h-40"
              hint="Fallback for when you only have the text. Leave empty to clear the CV."
              placeholder="Paste the candidate's CV text here…"
            />
            <div className="flex justify-end">
              <Button type="button" onClick={handleSaveText} disabled={savingText}>
                {savingText && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
                Save CV text
              </Button>
            </div>
          </div>
        )}

        {hasCv && !pasteOpen && (
          <details className="group">
            <summary className="text-ink-600 hover:text-ink-900 cursor-pointer text-xs font-medium">
              View extracted text
            </summary>
            <p className="text-ink-600 border-ink-200 mt-2 max-h-56 overflow-y-auto rounded-lg border p-3 text-xs whitespace-pre-wrap">
              {candidate.cv_text}
            </p>
          </details>
        )}
      </div>
    </section>
  )
}
