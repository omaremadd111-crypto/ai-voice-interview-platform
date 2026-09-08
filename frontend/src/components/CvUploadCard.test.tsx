import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as candidatesApi from '@/api/candidates'
import type { Candidate } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { CvUploadCard } from './CvUploadCard'

const CANDIDATE: Candidate = {
  id: 42,
  position_id: 7,
  full_name: 'Priya Raman',
  email: null,
  phone: null,
  cv_text: null,
  cv_filename: null,
  status: 'new',
}

function fileInput(): HTMLInputElement {
  return screen.getByLabelText(/choose a cv file/i) as HTMLInputElement
}

describe('CvUploadCard', () => {
  it('uploads a chosen PDF and reports the parsed result', async () => {
    const uploaded: Candidate = {
      ...CANDIDATE,
      cv_text: 'Extracted CV text from the PDF.',
      cv_filename: 'priya_cv.pdf',
    }
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv').mockResolvedValue(uploaded)
    const onUpdated = vi.fn()

    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={onUpdated} />)

    const file = new File([new Uint8Array([1, 2, 3])], 'priya_cv.pdf', { type: 'application/pdf' })
    await userEvent.upload(fileInput(), file)

    await waitFor(() => expect(upload).toHaveBeenCalledWith(42, file))
    expect(onUpdated).toHaveBeenCalledWith(uploaded)
    expect(await screen.findByRole('status')).toHaveTextContent('priya_cv.pdf')
  })

  it('rejects an unsupported file type before calling the API', async () => {
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv')
    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={vi.fn()} />)

    const file = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })
    // applyAccept: false bypasses the input's accept filter, which is what a real
    // user does by picking "All files" in the OS dialog -- this is exactly the
    // case the component's own extension guard exists to catch.
    await userEvent.upload(fileInput(), file, { applyAccept: false })

    expect(await screen.findByRole('alert')).toHaveTextContent(/unsupported file type/i)
    expect(upload).not.toHaveBeenCalled()
  })

  it('rejects a file over the size limit before calling the API', async () => {
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv')
    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={vi.fn()} />)

    // Report an oversized `size` rather than allocating megabytes in jsdom --
    // `size` is the only thing the guard reads.
    const oversized = new File([new Uint8Array([1, 2, 3])], 'big.pdf', { type: 'application/pdf' })
    Object.defineProperty(oversized, 'size', {
      value: candidatesApi.MAX_CV_UPLOAD_BYTES + 1,
    })
    await userEvent.upload(fileInput(), oversized)

    expect(await screen.findByRole('alert')).toHaveTextContent(/limit/i)
    expect(upload).not.toHaveBeenCalled()
  })

  it('surfaces a backend parse failure', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(candidatesApi, 'uploadCandidateCv').mockRejectedValue(
      new ApiError(400, "Could not read PDF 'broken.pdf': corrupted or invalid file"),
    )
    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={vi.fn()} />)

    const file = new File([new Uint8Array([1, 2])], 'broken.pdf', { type: 'application/pdf' })
    await userEvent.upload(fileInput(), file)

    expect(await screen.findByRole('alert')).toHaveTextContent(/corrupted or invalid file/i)
  })

  it('shows the CV state and filename when one is already stored', () => {
    const withCv: Candidate = {
      ...CANDIDATE,
      cv_text: 'Some extracted text',
      cv_filename: 'priya_cv.docx',
    }
    renderWithProviders(<CvUploadCard candidate={withCv} onUpdated={vi.fn()} />)

    expect(screen.getByText('Uploaded')).toBeInTheDocument()
    expect(screen.getByText('priya_cv.docx')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /replace cv/i })).toBeInTheDocument()
  })

  it('shows "No CV" when the candidate has none', () => {
    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={vi.fn()} />)
    expect(screen.getByText('No CV')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^upload cv$/i })).toBeInTheDocument()
  })

  it('keeps pasted text as a fallback path', async () => {
    const saved: Candidate = { ...CANDIDATE, cv_text: 'Pasted CV body', cv_filename: 'pasted-text' }
    const update = vi.spyOn(candidatesApi, 'updateCandidate').mockResolvedValue(saved)
    const onUpdated = vi.fn()

    renderWithProviders(<CvUploadCard candidate={CANDIDATE} onUpdated={onUpdated} />)

    await userEvent.click(screen.getByRole('button', { name: /paste cv text instead/i }))
    await userEvent.type(screen.getByLabelText(/cv text/i), 'Pasted CV body')
    await userEvent.click(screen.getByRole('button', { name: /save cv text/i }))

    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(update.mock.calls[0]![1]).toMatchObject({ cv_text: 'Pasted CV body' })
    expect(onUpdated).toHaveBeenCalledWith(saved)
  })
})
