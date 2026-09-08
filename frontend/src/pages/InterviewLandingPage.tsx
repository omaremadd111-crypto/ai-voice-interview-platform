import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { getInterviewLanding, getInterviewStatus, startInterview } from '@/api/public'
import type { InterviewLanding, InterviewStage } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { ErrorAlert, LoadingBlock, Spinner } from '@/components/ui/Feedback'
import { CheckIcon, ClockIcon } from '@/components/ui/Icons'
import { toMessage } from '@/lib/useAsync'

const POLL_INTERVAL_MS = 3000
const STAGES_THAT_STOP_POLLING: InterviewStage[] = ['ready', 'completed', 'failed']
// A couple of misses is a normal network blip and self-heals silently. Past
// that, staying silent forever is indistinguishable from actually being
// stuck -- see the Phase 2 handoff bug this guards against: any polling
// error (transient or not) used to retry forever with zero visible feedback.
const POLL_FAILURES_BEFORE_SHOWING_AN_ERROR = 3

const STAGE_COPY: Record<InterviewStage, { title: string; body: string }> = {
  not_started: {
    title: 'Ready when you are',
    body: 'This will start a short, AI-conducted voice interview. Find a quiet spot with a working microphone before you begin.',
  },
  preparing: {
    title: 'Preparing your interview…',
    body: 'This usually takes a few seconds. Please keep this page open.',
  },
  ready: {
    title: 'Your interview room is ready',
    body: 'Taking you there now…',
  },
  completed: {
    title: 'You’ve already completed this interview',
    body: 'Thank you — the hiring team has what they need and will follow up with next steps.',
  },
  failed: {
    title: 'This interview could not be completed',
    body: 'Please reach out to the hiring team so they can help.',
  },
}

/**
 * The durable interview-invitation landing page (/interview/:token). No
 * authentication -- the token itself is what proves this visitor is the
 * invited candidate. See application/interview_landing_service.py.
 */
export function InterviewLandingPage() {
  const { token } = useParams<{ token: string }>()

  const [landing, setLanding] = useState<InterviewLanding | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollFailuresRef = useRef(0)

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!token) return
    let active = true
    getInterviewLanding(token)
      .then((result) => {
        if (!active) return
        setLanding(result)
        if (!STAGES_THAT_STOP_POLLING.includes(result.stage) && result.stage !== 'not_started') {
          beginPolling(token)
        }
      })
      .catch((caught) => {
        if (active) setError(toMessage(caught, 'This interview link is not available.'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      stopPolling()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (landing?.stage === 'ready' && landing.voice_invite_url) {
      window.location.href = landing.voice_invite_url
    }
  }, [landing])

  function beginPolling(activeToken: string) {
    stopPolling()
    pollFailuresRef.current = 0
    setPollError(null)
    pollRef.current = setInterval(() => {
      getInterviewStatus(activeToken)
        .then((result) => {
          pollFailuresRef.current = 0
          setPollError(null)
          setLanding(result)
          if (STAGES_THAT_STOP_POLLING.includes(result.stage)) stopPolling()
        })
        .catch((caught) => {
          // A single missed poll is a normal network blip -- keep showing the
          // last known state and try again next tick. Only after several in a
          // row do we say so: silently retrying forever otherwise looks
          // identical to actually being stuck, with no way to tell them apart.
          pollFailuresRef.current += 1
          if (pollFailuresRef.current >= POLL_FAILURES_BEFORE_SHOWING_AN_ERROR) {
            setPollError(
              toMessage(caught, 'We lost contact while preparing your interview. Still trying…'),
            )
          }
        })
    }, POLL_INTERVAL_MS)
  }

  async function handleStart() {
    if (!token) return
    setError(null)
    setStarting(true)
    try {
      const result = await startInterview(token)
      setLanding(result)
      if (!STAGES_THAT_STOP_POLLING.includes(result.stage)) beginPolling(token)
    } catch (caught) {
      setError(toMessage(caught, 'Could not start the interview. Please try again.'))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="bg-surface-sunken min-h-screen">
      <div className="mx-auto max-w-lg px-5 py-16 sm:py-24">
        {loading && (
          <Card className="p-8">
            <LoadingBlock label="Loading your interview…" />
          </Card>
        )}

        {!loading && (error !== null || landing === null) && (
          <Card className="p-8 text-center">
            <h1 className="text-ink-900 text-lg font-semibold">This interview link isn’t available</h1>
            <p className="text-ink-500 mt-2 text-sm">
              It may have expired or already been used. Contact the hiring team for a new link.
            </p>
          </Card>
        )}

        {!loading && landing !== null && (
          <Card className="p-8 text-center sm:p-10">
            <p className="text-brand-600 text-xs font-semibold tracking-[0.18em] uppercase">
              {landing.company_name}
            </p>
            <h1 className="text-ink-900 mt-2 text-xl font-semibold">
              Hi {landing.candidate_first_name} — {landing.position_title}
            </h1>

            {error && <ErrorAlert message={error} className="mt-5 text-left" />}

            <div className="mt-6">
              <h2 className="text-ink-800 text-sm font-semibold">{STAGE_COPY[landing.stage].title}</h2>

              {landing.stage === 'not_started' && (
                <>
                  <p className="text-ink-500 mt-2 text-sm leading-6">{STAGE_COPY.not_started.body}</p>
                  <Button
                    className="mt-5 w-full"
                    onClick={() => void handleStart()}
                    disabled={starting}
                    loading={starting}
                  >
                    Start interview
                  </Button>
                </>
              )}

              {landing.stage === 'preparing' && (
                <div className="mt-3 flex flex-col items-center gap-3 py-2">
                  <Spinner className="border-ink-300 border-t-brand-600 h-6 w-6" />
                  <p className="text-ink-500 text-sm">{STAGE_COPY.preparing.body}</p>
                  {pollError && <ErrorAlert message={pollError} className="mt-2 w-full text-left" />}
                </div>
              )}

              {landing.stage === 'ready' && (
                <div className="mt-3 flex flex-col items-center gap-3 py-2">
                  <Spinner className="border-ink-300 border-t-brand-600 h-6 w-6" />
                  <p className="text-ink-500 text-sm">{STAGE_COPY.ready.body}</p>
                </div>
              )}

              {landing.stage === 'completed' && (
                <div className="mt-3 flex flex-col items-center gap-3 py-2">
                  <div className="bg-success-50 text-success-600 flex h-10 w-10 items-center justify-center rounded-full">
                    <CheckIcon className="h-5 w-5" />
                  </div>
                  <p className="text-ink-500 text-sm">{STAGE_COPY.completed.body}</p>
                </div>
              )}

              {landing.stage === 'failed' && (
                <div className="mt-3 flex flex-col items-center gap-3 py-2">
                  <ClockIcon className="text-ink-400 h-8 w-8" />
                  <p className="text-ink-500 text-sm">{STAGE_COPY.failed.body}</p>
                </div>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
