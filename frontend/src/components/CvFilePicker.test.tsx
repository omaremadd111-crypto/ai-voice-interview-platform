import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { validateCvFile } from '@/lib/cvFile'
import { CvFilePicker } from './CvFilePicker'

function pdf(name = 'cv.pdf', bytes: BlobPart = new Uint8Array([1, 2, 3])) {
  return new File([bytes], name, { type: 'application/pdf' })
}

/** A drag payload carrying real files -- matches what a browser reports for an
 *  OS file drag (`dataTransfer.types` includes `'Files'`) closely enough for
 *  the component's own `isFileDrag` check, which is all it reads pre-drop. */
function filesPayload(files: File[]) {
  return { dataTransfer: { files, types: ['Files'] } }
}

/** Mirrors how the real Add Candidate form uses CvFilePicker: a controlled
 *  `file` prop fed back through the same validate-then-accept-or-reject
 *  onSelect the real form uses (CandidatesPage's handleSelectFile), not a
 *  bare passthrough -- so these tests see the same "rejected files never
 *  become the selected file" behavior production does. */
function Harness({ disabled = false }: { disabled?: boolean }) {
  const [file, setFile] = useState<File | null>(null)
  function handleSelect(candidate: File | null) {
    setFile(candidate !== null && validateCvFile(candidate) === null ? candidate : null)
  }
  return <CvFilePicker file={file} onSelect={handleSelect} disabled={disabled} />
}

function dropzone() {
  return screen.getByRole('button', { name: /upload cv file/i })
}

describe('CvFilePicker', () => {
  it('shows the drop zone with accepted formats and the size limit', () => {
    render(<Harness />)
    expect(screen.getByText(/drag & drop your cv here/i)).toBeInTheDocument()
    expect(screen.getByText(/choose a file/i)).toBeInTheDocument()
    expect(screen.getByText(/\.pdf, \.docx, \.txt, \.md/)).toBeInTheDocument()
    expect(screen.getByText(/5\.0 MB/)).toBeInTheDocument()
  })

  it('opens the native file picker when the drop zone is clicked', async () => {
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    render(<Harness />)

    await userEvent.click(dropzone())
    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it('opens the file picker from the keyboard, proving the zone is a real focusable control', async () => {
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    render(<Harness />)

    await userEvent.tab()
    expect(dropzone()).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it('selects a valid file dropped onto the zone', () => {
    render(<Harness />)
    const file = pdf('resume.pdf')

    fireEvent.dragEnter(dropzone(), filesPayload([file]))
    fireEvent.drop(dropzone(), filesPayload([file]))

    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /remove selected file/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /replace/i })).toBeInTheDocument()
  })

  it('shows drop wording and clears it once the drag leaves', () => {
    render(<Harness />)
    const file = pdf()

    fireEvent.dragEnter(dropzone(), filesPayload([file]))
    expect(screen.getByText(/^drop your cv here$/i)).toBeInTheDocument()

    fireEvent.dragLeave(dropzone(), filesPayload([file]))
    expect(screen.getByText(/drag & drop your cv here/i)).toBeInTheDocument()
  })

  it('keeps the highlighted state through nested enter/leave pairs without flicker', () => {
    render(<Harness />)
    const file = pdf()
    const zone = dropzone()
    const icon = zone.querySelector('svg') as Element

    // A drag crossing a child element inside the zone fires an extra
    // enter/leave pair -- the zone must stay highlighted throughout, not
    // flicker back to idle in between.
    fireEvent.dragEnter(zone, filesPayload([file]))
    fireEvent.dragEnter(icon, filesPayload([file]))
    fireEvent.dragLeave(icon, filesPayload([file]))
    expect(screen.getByText(/^drop your cv here$/i)).toBeInTheDocument()

    fireEvent.dragLeave(zone, filesPayload([file]))
    expect(screen.getByText(/drag & drop your cv here/i)).toBeInTheDocument()
  })

  it('calls onSelect with the dropped file even when it will be rejected, unchanged from the click path', () => {
    const onSelect = vi.fn()
    render(<CvFilePicker file={null} onSelect={onSelect} />)
    const bad = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })

    fireEvent.drop(dropzone(), filesPayload([bad]))

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(bad)
  })

  it('shows an inline error for an unsupported file type', () => {
    render(<Harness />)
    const bad = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })

    fireEvent.drop(dropzone(), filesPayload([bad]))

    expect(screen.getByRole('alert')).toHaveTextContent(/unsupported file type/i)
    // The parent (Harness, mirroring the real form) rejected it too, so the
    // zone stays in its empty state -- not showing a "selected" chip for a
    // file nothing accepted.
    expect(screen.queryByText('resume.exe')).not.toBeInTheDocument()
  })

  it('shows an inline error for a file over the 5 MB limit', () => {
    render(<Harness />)
    const big = pdf('huge.pdf', new Uint8Array(5 * 1024 * 1024 + 1))

    fireEvent.drop(dropzone(), filesPayload([big]))

    expect(screen.getByRole('alert')).toHaveTextContent(/over the 5\.0 MB limit/i)
  })

  it('clears the inline error once a valid file replaces the rejected one', () => {
    render(<Harness />)
    const bad = new File([new Uint8Array([1])], 'resume.exe', { type: 'application/octet-stream' })
    fireEvent.drop(dropzone(), filesPayload([bad]))
    expect(screen.getByRole('alert')).toHaveTextContent(/unsupported file type/i)

    fireEvent.drop(dropzone(), filesPayload([pdf('resume.pdf')]))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
  })

  it('removes the selected file and returns to the drop zone', async () => {
    render(<Harness />)
    fireEvent.drop(dropzone(), filesPayload([pdf('resume.pdf')]))
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /remove selected file/i }))

    expect(screen.queryByText('resume.pdf')).not.toBeInTheDocument()
    expect(dropzone()).toBeInTheDocument()
  })

  it('opens the file picker to replace an already-selected file', async () => {
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    render(<Harness />)
    fireEvent.drop(dropzone(), filesPayload([pdf('resume.pdf')]))

    await userEvent.click(screen.getByRole('button', { name: /replace/i }))

    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it('replaces a selected file by dropping a new one on the chip', () => {
    render(<Harness />)
    fireEvent.drop(dropzone(), filesPayload([pdf('first.pdf')]))
    expect(screen.getByText('first.pdf')).toBeInTheDocument()

    const chip = screen.getByText('first.pdf').closest('div') as Element
    fireEvent.drop(chip, filesPayload([pdf('second.pdf')]))

    expect(screen.queryByText('first.pdf')).not.toBeInTheDocument()
    expect(screen.getByText('second.pdf')).toBeInTheDocument()
  })

  it('ignores drops and clicks while disabled', async () => {
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    const onSelect = vi.fn()
    render(<CvFilePicker file={null} onSelect={onSelect} disabled />)

    await userEvent.click(dropzone())
    expect(clickSpy).not.toHaveBeenCalled()

    fireEvent.drop(dropzone(), filesPayload([pdf()]))
    expect(onSelect).not.toHaveBeenCalled()

    clickSpy.mockRestore()
  })

  it('still exposes the hidden input under its existing accessible name, for callers that select a file directly', async () => {
    render(<Harness />)
    const input = screen.getByLabelText(/choose a cv file/i)

    await userEvent.upload(input, pdf('direct.pdf'))

    expect(screen.getByText('direct.pdf')).toBeInTheDocument()
  })
})
