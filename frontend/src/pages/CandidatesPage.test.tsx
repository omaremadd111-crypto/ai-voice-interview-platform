import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as candidatesApi from '@/api/candidates'
import * as positionsApi from '@/api/positions'
import type { Candidate, Position } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { CandidatesPage } from './CandidatesPage'

const POSITION: Position = {
  id: 7,
  owner_id: 1,
  company_name: 'Northwind Labs',
  title: 'Junior AI Engineer',
  description: 'Build RAG systems.',
  experience_level: 'Junior',
  pass_score_threshold: 65,
  rubric_profile: null,
  status: 'active',
}

const CREATED: Candidate = {
  id: 42,
  position_id: 7,
  full_name: 'Jordan Rivera',
  email: null,
  phone: null,
  cv_text: null,
  cv_filename: null,
  status: 'new',
}

function stubLists() {
  vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([POSITION])
  vi.spyOn(candidatesApi, 'listAllCandidates').mockResolvedValue([])
}

async function openModal() {
  const openButtons = await screen.findAllByRole('button', { name: /add candidate/i })
  await userEvent.click(openButtons[0]!)
  return screen.findByRole('dialog')
}

async function fillName(dialog: HTMLElement, name = 'Jordan Rivera') {
  await userEvent.type(within(dialog).getByLabelText(/full name/i), name)
}

function pdf(name = 'jordan_cv.pdf') {
  return new File([new Uint8Array([1, 2, 3])], name, { type: 'application/pdf' })
}

describe('CandidatesPage — add candidate with CV upload', () => {
  it('creates the candidate then uploads the chosen file', async () => {
    stubLists()
    const create = vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv').mockResolvedValue({
      ...CREATED,
      cv_text: 'Parsed text',
      cv_filename: 'jordan_cv.pdf',
    })

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)

    const file = pdf()
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), file)
    expect(within(dialog).getByText('jordan_cv.pdf')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    await waitFor(() => expect(upload).toHaveBeenCalledWith(42, file))
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Jordan Rivera was added and “jordan_cv.pdf” was parsed/i,
    )
  })

  it('creates the candidate then uploads a file dragged onto the drop zone', async () => {
    stubLists()
    const create = vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv').mockResolvedValue({
      ...CREATED,
      cv_text: 'Parsed text',
      cv_filename: 'jordan_cv.pdf',
    })

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)

    const file = pdf()
    const dropzone = within(dialog).getByRole('button', { name: /upload cv file/i })
    fireEvent.drop(dropzone, { dataTransfer: { files: [file], types: ['Files'] } })
    expect(within(dialog).getByText('jordan_cv.pdf')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    await waitFor(() => expect(upload).toHaveBeenCalledWith(42, file))
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Jordan Rivera was added and “jordan_cv.pdf” was parsed/i,
    )
  })

  it('creates the candidate without touching upload when no file is chosen', async () => {
    stubLists()
    vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv')

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(/Jordan Rivera was added\./i)
    expect(upload).not.toHaveBeenCalled()
  })

  it('rejects an unsupported file without creating anything', async () => {
    stubLists()
    const create = vi.spyOn(candidatesApi, 'createCandidate')

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)

    const bad = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), bad, {
      applyAccept: false,
    })

    expect((await within(dialog).findAllByRole('alert'))[0]).toHaveTextContent(
      /unsupported file type/i,
    )
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))
    expect(create).not.toHaveBeenCalled()

    // The blocking message tells HR how to proceed either way.
    const alerts = await within(dialog).findAllByRole('alert')
    expect(alerts.some((a) => /remove it to continue without a CV/i.test(a.textContent ?? ''))).toBe(
      true,
    )
  })

  it('unblocks submission once the rejected file is removed', async () => {
    stubLists()
    const create = vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)

    const bad = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), bad, {
      applyAccept: false,
    })
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))
    expect(create).not.toHaveBeenCalled()

    // Choosing a valid file clears the block.
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), pdf())
    vi.spyOn(candidatesApi, 'uploadCandidateCv').mockResolvedValue(CREATED)
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
  })

  it('reports a partial success when the candidate is created but the CV fails', async () => {
    const { ApiError } = await import('@/api/client')
    stubLists()
    vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)
    vi.spyOn(candidatesApi, 'uploadCandidateCv').mockRejectedValue(
      new ApiError(400, "Could not read PDF 'jordan_cv.pdf': corrupted or invalid file"),
    )

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), pdf())
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Jordan Rivera was added, but the CV could not be uploaded/i)
    expect(alert).toHaveTextContent(/corrupted or invalid file/i)
    expect(alert).toHaveTextContent(/upload it from their profile/i)
  })

  it('keeps the candidate-creation error in the modal and does not upload', async () => {
    const { ApiError } = await import('@/api/client')
    stubLists()
    vi.spyOn(candidatesApi, 'createCandidate').mockRejectedValue(
      new ApiError(403, 'You do not have access to this resource'),
    )
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv')

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), pdf())
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/do not have access/i)
    expect(upload).not.toHaveBeenCalled()
  })

  it('lets the chosen file be removed before submitting', async () => {
    stubLists()
    vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)
    const upload = vi.spyOn(candidatesApi, 'uploadCandidateCv')

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)
    await userEvent.upload(within(dialog).getByLabelText(/choose a cv file/i), pdf())
    expect(within(dialog).getByText('jordan_cv.pdf')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: /remove selected file/i }))
    expect(within(dialog).queryByText('jordan_cv.pdf')).not.toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument())
    expect(upload).not.toHaveBeenCalled()
  })

  it('still offers pasted CV text as a fallback', async () => {
    stubLists()
    const create = vi.spyOn(candidatesApi, 'createCandidate').mockResolvedValue(CREATED)

    renderWithProviders(<CandidatesPage />)
    const dialog = await openModal()
    await fillName(dialog)
    await userEvent.click(within(dialog).getByText(/paste cv text instead/i))
    await userEvent.type(within(dialog).getByLabelText(/cv text/i), 'Pasted CV body')
    await userEvent.click(within(dialog).getByRole('button', { name: /^add candidate$/i }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    expect(create.mock.calls[0]![0]).toMatchObject({ cv_text: 'Pasted CV body' })
  })
})
