import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as screeningConfigApi from '@/api/screeningConfig'
import type { ScreeningConfig } from '@/api/types'
import { renderWithProviders } from '@/test/renderWithProviders'
import { ScreeningSetupPanel } from './ScreeningSetupPanel'

const DRAFT_CONFIG: ScreeningConfig = {
  position_id: 7,
  template_status: 'draft',
  template_approved_at: null,
  accept_public_applications: false,
  auto_parse_cv: true,
  auto_create_plan: true,
  auto_create_invitation: true,
  allow_immediate_start: true,
  auto_email_invitation: false,
  allow_cv_personalization: false,
  invitation_ttl_hours: 168,
  reminder_offsets_hours: [],
  max_applications_per_day: null,
  require_phone: false,
  application_notice: null,
  public_slug: null,
  public_url: null,
}

describe('ScreeningSetupPanel', () => {
  it('disables Approve with a reason when the position has no questions yet', async () => {
    vi.spyOn(screeningConfigApi, 'getScreeningConfig').mockResolvedValue(DRAFT_CONFIG)

    renderWithProviders(<ScreeningSetupPanel positionId={7} questionCount={0} />)

    const approveButton = await screen.findByRole('button', { name: /approve template/i })
    expect(approveButton).toBeDisabled()
    expect(screen.getByText(/add at least one interview question first/i)).toBeInTheDocument()
  })

  it('approves the template, then publishes and shows the copyable application link', async () => {
    vi.spyOn(screeningConfigApi, 'getScreeningConfig').mockResolvedValue(DRAFT_CONFIG)
    const approve = vi.spyOn(screeningConfigApi, 'approveScreeningTemplate').mockResolvedValue({
      ...DRAFT_CONFIG,
      template_status: 'approved',
      template_approved_at: '2026-09-01T00:00:00Z',
    })
    const publish = vi.spyOn(screeningConfigApi, 'publishPosition').mockResolvedValue({
      ...DRAFT_CONFIG,
      template_status: 'approved',
      template_approved_at: '2026-09-01T00:00:00Z',
      accept_public_applications: true,
      public_slug: 'junior-ai-engineer-ab12cd34',
      public_url: 'http://localhost:5173/jobs/junior-ai-engineer-ab12cd34',
    })

    renderWithProviders(<ScreeningSetupPanel positionId={7} questionCount={3} />)

    await userEvent.click(await screen.findByRole('button', { name: /approve template/i }))
    expect(approve).toHaveBeenCalledWith(7)
    expect(await screen.findByText('Approved')).toBeInTheDocument()

    await userEvent.click(await screen.findByRole('button', { name: /^publish$/i }))
    expect(publish).toHaveBeenCalledWith(7)

    expect(
      await screen.findByDisplayValue('http://localhost:5173/jobs/junior-ai-engineer-ab12cd34'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open job page/i })).toHaveAttribute(
      'href',
      'http://localhost:5173/jobs/junior-ai-engineer-ab12cd34',
    )
  })

  it('unpublishing keeps the slug but stops accepting applications', async () => {
    const published: ScreeningConfig = {
      ...DRAFT_CONFIG,
      template_status: 'approved',
      accept_public_applications: true,
      public_slug: 'junior-ai-engineer-ab12cd34',
      public_url: 'http://localhost:5173/jobs/junior-ai-engineer-ab12cd34',
    }
    vi.spyOn(screeningConfigApi, 'getScreeningConfig').mockResolvedValue(published)
    const unpublish = vi.spyOn(screeningConfigApi, 'unpublishPosition').mockResolvedValue({
      ...published,
      accept_public_applications: false,
    })

    renderWithProviders(<ScreeningSetupPanel positionId={7} questionCount={3} />)

    await userEvent.click(await screen.findByRole('button', { name: /stop accepting applications/i }))
    expect(unpublish).toHaveBeenCalledWith(7)
    expect(await screen.findByText(/not currently accepting applications/i)).toBeInTheDocument()
  })
})
