import {
  BarVisualizer,
  DisconnectButton,
  LiveKitRoom,
  RoomAudioRenderer,
  TrackToggle,
  useConnectionState,
  useRoomContext,
  useVoiceAssistant,
} from '@livekit/components-react'
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'
import { ConnectionState, RoomEvent, Track } from 'livekit-client'

import { exchangeVoiceInvitation } from '@/api/voice'
import type { VoiceRoomToken } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { ErrorAlert, Spinner } from '@/components/ui/Feedback'
import { toMessage } from '@/lib/useAsync'

const STATE_LABEL: Record<string, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting securely…',
  connected: 'Connected',
  reconnecting: 'Connection interrupted — reconnecting…',
}

const INTERVIEW_LIFECYCLE_TOPIC = 'hr.interview.lifecycle'

function isInterviewCompletedMessage(payload: Uint8Array, topic?: string): boolean {
  if (topic !== INTERVIEW_LIFECYCLE_TOPIC) return false
  try {
    const message: unknown = JSON.parse(new TextDecoder().decode(payload))
    return (
      typeof message === 'object' &&
      message !== null &&
      'type' in message &&
      message.type === 'interview_completed'
    )
  } catch {
    return false
  }
}

function VoiceRoomContent({
  agentName,
  companyName,
  onCompleted,
}: {
  agentName: string
  companyName: string
  onCompleted: () => void
}) {
  const connection = useConnectionState()
  const room = useRoomContext()
  const { state, audioTrack } = useVoiceAssistant()
  const [text, setText] = useState('')
  const [sendError, setSendError] = useState<string | null>(null)

  useEffect(() => {
    function handleData(payload: Uint8Array, _participant?: unknown, _kind?: unknown, topic?: string) {
      if (!isInterviewCompletedMessage(payload, topic)) return
      onCompleted()
      void room.disconnect()
    }

    room.on(RoomEvent.DataReceived, handleData)
    return () => {
      room.off(RoomEvent.DataReceived, handleData)
    }
  }, [onCompleted, room])

  async function sendText(event: FormEvent) {
    event.preventDefault()
    const answer = text.trim()
    if (!answer) return
    setSendError(null)
    try {
      await room.localParticipant.sendText(answer, { topic: 'lk.chat' })
      setText('')
    } catch {
      setSendError('Your typed response could not be sent. Please try again.')
    }
  }

  const connectionLabel = STATE_LABEL[connection] ?? connection
  const agentLabel =
    state === 'speaking'
      ? `${agentName} is speaking`
      : state === 'listening'
        ? `${agentName} is listening`
        : state === 'thinking'
          ? `${agentName} is preparing the next question`
          : `${agentName} is ready`

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center px-5 py-10">
      <div className="card overflow-hidden">
        <div className="bg-ink-950 px-6 py-7 text-white sm:px-10">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-brand-300 text-xs font-semibold tracking-[0.18em] uppercase">
                AI voice screening
              </p>
              <h1 className="mt-2 text-2xl font-semibold">Interview with {agentName}</h1>
              <p className="mt-1 text-xs text-slate-400">{companyName}</p>
            </div>
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs" role="status">
              {connectionLabel}
            </span>
          </div>
          <p className="mt-3 max-w-xl text-sm text-slate-300">
            Speak naturally. You can pause, ask {agentName} to repeat a question, or interrupt
            while {agentName} is speaking.
          </p>
        </div>

        <div className="space-y-7 px-6 py-8 sm:px-10">
          <div className="bg-brand-50 border-brand-100 rounded-2xl border px-5 py-7 text-center">
            <div
              className="mx-auto flex h-24 max-w-sm items-center justify-center gap-1"
              aria-hidden="true"
            >
              <BarVisualizer state={state} track={audioTrack} barCount={7} className="flex h-20 items-center gap-1">
                <span className="bg-brand-500 block w-2 rounded-full data-[lk-highlighted=true]:h-16 h-6 transition-all" />
              </BarVisualizer>
            </div>
            <p className="text-ink-800 mt-3 font-medium" aria-live="polite">
              {agentLabel}
            </p>
          </div>

          {connection === ConnectionState.Reconnecting && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
              We lost the network briefly. LiveKit is reconnecting this interview automatically.
            </div>
          )}
          {sendError && <ErrorAlert message={sendError} />}

          <div className="flex flex-wrap items-center justify-center gap-3">
            <TrackToggle
              source={Track.Source.Microphone}
              className="border-ink-300 text-ink-700 hover:bg-ink-50 rounded-lg border bg-white px-4 py-2 text-sm font-medium"
            >
              Microphone
            </TrackToggle>
            <DisconnectButton className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
              Leave interview
            </DisconnectButton>
          </div>

          <form onSubmit={sendText} className="border-ink-200 border-t pt-6">
            <label htmlFor="typed-answer" className="text-ink-800 text-sm font-medium">
              Can’t use your microphone? Type your response
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="typed-answer"
                value={text}
                onChange={(event) => setText(event.target.value)}
                disabled={connection !== ConnectionState.Connected}
                className="border-ink-300 focus:border-brand-500 min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm"
                placeholder="Type the same answer you would say aloud"
              />
              <Button type="submit" disabled={!text.trim() || connection !== ConnectionState.Connected}>
                Send
              </Button>
            </div>
          </form>

          <p className="text-ink-500 text-center text-xs">
            This interview is reviewed by a human recruiter. Voice characteristics are not used
            for screening or evaluation.
          </p>
        </div>
      </div>
      <RoomAudioRenderer />
    </div>
  )
}

export function VoiceInterviewPage() {
  const { invitation = '' } = useParams<{ invitation: string }>()
  const [credential, setCredential] = useState<VoiceRoomToken | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [connect, setConnect] = useState(false)
  const [completed, setCompleted] = useState(false)
  const completedRef = useRef(false)

  const finishInterview = useCallback(() => {
    completedRef.current = true
    setCompleted(true)
    setConnect(false)
    setError(null)
  }, [])

  async function start() {
    completedRef.current = false
    setCompleted(false)
    setConnecting(true)
    setError(null)
    try {
      const roomCredential = await exchangeVoiceInvitation(invitation)
      setCredential(roomCredential)
      setConnect(true)
    } catch (caught) {
      setError(toMessage(caught, 'This interview link could not be opened.'))
    } finally {
      setConnecting(false)
    }
  }

  if (completed) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl items-center px-5 py-12">
        <div className="card w-full p-7 text-center sm:p-10">
          <p className="text-brand-700 text-xs font-semibold tracking-[0.18em] uppercase">
            Voice interview complete
          </p>
          <h1 className="text-ink-950 mt-3 text-3xl font-semibold">
            Your interview has been completed successfully.
          </h1>
          <p className="text-ink-600 mt-4 text-sm leading-6">
            Thank you for your time. You may now close this window.
          </p>
        </div>
      </main>
    )
  }

  if (credential === null) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl items-center px-5 py-12">
        <div className="card w-full p-7 sm:p-10">
          <p className="text-brand-700 text-xs font-semibold tracking-[0.18em] uppercase">
            Voice interview
          </p>
          <h1 className="text-ink-950 mt-3 text-3xl font-semibold">Ready for your interview?</h1>
          <p className="text-ink-600 mt-4 text-sm leading-6">
            Your interviewer is an AI screening assistant. Starting will request microphone permission and
            connect you to a private LiveKit room. A human recruiter reviews the interview.
          </p>
          <ul className="text-ink-600 mt-5 space-y-2 text-sm">
            <li>• Find a quiet place and allow microphone access.</li>
            <li>• You can ask for a pause or for any question to be repeated.</li>
            <li>• If the network drops, the interview reconnects automatically.</li>
          </ul>
          {error && <ErrorAlert message={error} className="mt-5" />}
          <Button className="mt-7 w-full" onClick={start} disabled={connecting || !invitation}>
            {connecting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
            Start voice interview
          </Button>
        </div>
      </main>
    )
  }

  return (
    <LiveKitRoom
      token={credential.token}
      serverUrl={credential.server_url}
      connect={connect}
      audio
      video={false}
      onDisconnected={() => {
        if (completedRef.current) return
        setConnect(false)
        setCredential(null)
        setError('The room disconnected. Use the button below to reconnect with a fresh token.')
      }}
      onError={() => setError('The voice connection failed. Check your network and try again.')}
      onMediaDeviceFailure={() => setError('Microphone access failed. Check browser permission and try again.')}
    >
      {error && (
        <div className="fixed inset-x-4 top-4 z-10 mx-auto max-w-2xl">
          <ErrorAlert message={error} />
        </div>
      )}
      <VoiceRoomContent
        agentName={credential.agent_name}
        companyName={credential.company_name}
        onCompleted={finishInterview}
      />
    </LiveKitRoom>
  )
}
