import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VoiceInterviewPage } from './VoiceInterviewPage'

const exchange = vi.fn()
const consentNotice = vi.fn()
const recordConsent = vi.fn()
const roomHandlers = new Map<string, (...args: unknown[]) => void>()
const roomDisconnect = vi.fn()
let liveKitRoomProps: Record<string, unknown> = {}

vi.mock('@/api/voice', () => ({
  exchangeVoiceInvitation: (...args: unknown[]) => exchange(...args),
  fetchConsentNotice: () => consentNotice(),
  recordRecordingConsent: (...args: unknown[]) => recordConsent(...args),
}))

vi.mock('livekit-client', () => ({
  ConnectionState: { Connected: 'connected', Reconnecting: 'reconnecting' },
  RoomEvent: { DataReceived: 'dataReceived' },
  Track: { Source: { Microphone: 'microphone' } },
}))

vi.mock('@livekit/components-react', () => ({
  LiveKitRoom: (props: { children: React.ReactNode }) => {
    liveKitRoomProps = props as unknown as Record<string, unknown>
    return <div>{props.children}</div>
  },
  RoomAudioRenderer: () => null,
  BarVisualizer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TrackToggle: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  DisconnectButton: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  useConnectionState: () => 'connected',
  useRoomContext: () => ({
    localParticipant: { sendText: vi.fn() },
    disconnect: roomDisconnect,
    on: vi.fn((event: string, handler: (...args: unknown[]) => void) => roomHandlers.set(event, handler)),
    off: vi.fn((event: string) => roomHandlers.delete(event)),
  }),
  useVoiceAssistant: () => ({ state: 'listening', audioTrack: undefined }),
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/voice/signed-invitation']}>
      <Routes>
        <Route path="/voice/:invitation" element={<VoiceInterviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}


/** Click Start. The consent step is currently bypassed (dev/testing): the
 *  button is enabled as soon as an invitation is present, with no notice to
 *  wait for and no checkbox to satisfy. */
async function startInterview() {
  const button = await screen.findByRole('button', { name: /start voice interview/i })
  fireEvent.click(button)
}

describe('VoiceInterviewPage', () => {
  beforeEach(() => {
    exchange.mockReset()
    // consentNotice/recordConsent are never wired to this page's imports
    // anymore -- kept as spies purely so tests can assert they stay uncalled.
    consentNotice.mockReset()
    recordConsent.mockReset()
    roomHandlers.clear()
    roomDisconnect.mockReset()
    liveKitRoomProps = {}
  })

  it('exchanges the signed invitation only after the candidate starts', async () => {
    exchange.mockResolvedValue({
      token: 'jwt',
      server_url: 'wss://example.livekit.cloud',
      room_name: 'room',
      participant_identity: 'candidate-1',
      participant_name: 'Sam',
      agent_name: 'Aimy',
      company_name: 'Acme',
    })
    renderPage()

    expect(screen.getByText(/Your interviewer is an AI screening assistant/i)).toBeInTheDocument()
    await startInterview()

    await waitFor(() => expect(exchange).toHaveBeenCalledWith('signed-invitation'))
    expect(await screen.findByText('Interview with Aimy')).toBeInTheDocument()
    expect(screen.getByText(/Voice characteristics are not used/i)).toBeInTheDocument()
  })

  it('shows a safe error when an invitation cannot be exchanged', async () => {
    exchange.mockRejectedValue(new Error('The voice invitation has expired'))
    renderPage()

    await startInterview()

    expect(await screen.findByText('The voice invitation has expired')).toBeInTheDocument()
  })

  it('disconnects automatically and shows a terminal success screen after natural completion', async () => {
    exchange.mockResolvedValue({
      token: 'jwt',
      server_url: 'wss://example.livekit.cloud',
      room_name: 'room',
      participant_identity: 'candidate-1',
      participant_name: 'Sam',
      agent_name: 'Aimy',
      company_name: 'Acme',
    })
    renderPage()
    await startInterview()
    await screen.findByText('Interview with Aimy')
    await waitFor(() => expect(roomHandlers.has('dataReceived')).toBe(true))

    const completion = new TextEncoder().encode('{"type":"interview_completed"}')
    await act(async () => {
      roomHandlers.get('dataReceived')?.(completion, undefined, undefined, 'hr.interview.lifecycle')
    })

    await waitFor(() => expect(roomDisconnect).toHaveBeenCalledOnce())
    expect(
      screen.getByRole('heading', { name: 'Your interview has been completed successfully.' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /leave interview/i })).not.toBeInTheDocument()

    act(() => {
      ;(liveKitRoomProps.onDisconnected as (() => void) | undefined)?.()
    })
    expect(screen.queryByText(/reconnect with a fresh token/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start voice interview/i })).not.toBeInTheDocument()
  })

  it('keeps reconnect guidance for an unexpected disconnect during an active interview', async () => {
    exchange.mockResolvedValue({
      token: 'jwt',
      server_url: 'wss://example.livekit.cloud',
      room_name: 'room',
      participant_identity: 'candidate-1',
      participant_name: 'Sam',
      agent_name: 'Aimy',
      company_name: 'Acme',
    })
    renderPage()
    await startInterview()
    await screen.findByText('Interview with Aimy')

    act(() => {
      ;(liveKitRoomProps.onDisconnected as (() => void) | undefined)?.()
    })

    expect(screen.getByText(/reconnect with a fresh token/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start voice interview/i })).toBeInTheDocument()
  })
})

describe('VoiceInterviewPage recording consent (temporarily bypassed)', () => {
  beforeEach(() => {
    exchange.mockReset()
    consentNotice.mockReset()
    recordConsent.mockReset()
    roomHandlers.clear()
    roomDisconnect.mockReset()
    liveKitRoomProps = {}
    exchange.mockResolvedValue({
      token: 'jwt',
      server_url: 'wss://example.livekit.cloud',
      room_name: 'hr-screen-s1',
      participant_identity: 'candidate-1',
      participant_name: 'Omar',
      agent_name: 'Aimy',
      company_name: 'FlairsTech',
    })
  })

  it('renders no consent notice or checkbox on the pre-join screen', async () => {
    renderPage()

    await screen.findByRole('button', { name: /start voice interview/i })
    expect(screen.queryByText(/recording notice/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('the start button is enabled immediately, with no notice to wait for', async () => {
    renderPage()

    const button = await screen.findByRole('button', { name: /start voice interview/i })
    expect(button).not.toBeDisabled()
  })

  it('starts the interview on click without any consent API call', async () => {
    renderPage()
    await startInterview()

    await waitFor(() => expect(exchange).toHaveBeenCalledWith('signed-invitation'))
    expect(consentNotice).not.toHaveBeenCalled()
    expect(recordConsent).not.toHaveBeenCalled()
  })

  it('never requests the consent-notice endpoint on page load', async () => {
    renderPage()
    await screen.findByRole('button', { name: /start voice interview/i })

    // Give any stray effect a tick to fire before asserting silence.
    await act(async () => {
      await Promise.resolve()
    })
    expect(consentNotice).not.toHaveBeenCalled()
  })
})
