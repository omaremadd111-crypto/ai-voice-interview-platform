import { useEffect, useState } from 'react'

import { fetchInterviewMedia } from '@/api/voice'
import type { InterviewMedia, InterviewRecording } from '@/api/types'

function formatDuration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return '—'
  const total = Math.round(seconds)
  const minutes = Math.floor(total / 60)
  const remainder = total % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

function formatSize(bytes: number | null): string {
  if (bytes === null) return '—'
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Recording state that is not a playable file still tells HR something useful,
 *  so each one gets its own explanation rather than a blank panel. */
const STATUS_MESSAGE: Record<InterviewRecording['status'], string> = {
  pending: 'The recording was requested but has not started yet.',
  active: 'This interview is being recorded right now. Audio appears when it finishes.',
  completed: '',
  failed: 'The recording failed. The interview itself is unaffected — the transcript below is complete.',
  aborted: 'The recording was stopped before it finished. Any partial audio is unavailable.',
}

function RecordingSection({ recording }: { recording: InterviewRecording | null }) {
  if (recording === null) {
    return (
      <p className="text-ink-500 text-sm">
        No recording was made for this interview.
      </p>
    )
  }

  const playable = recording.status === 'completed' && recording.audio_url !== null

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <span className="text-ink-600">
          Status: <span className="text-ink-900 font-medium">{recording.status}</span>
        </span>
        <span className="text-ink-600">
          Duration:{' '}
          <span className="text-ink-900 font-medium">
            {formatDuration(recording.duration_seconds)}
          </span>
        </span>
        <span className="text-ink-600">
          Size: <span className="text-ink-900 font-medium">{formatSize(recording.file_size_bytes)}</span>
        </span>
      </div>

      {playable ? (
        <audio className="mt-4 w-full" controls preload="none" src={recording.audio_url ?? undefined}>
          Your browser cannot play this recording.
        </audio>
      ) : (
        <p className="text-ink-500 mt-4 text-sm">
          {STATUS_MESSAGE[recording.status] ||
            'The audio file is not available for playback.'}
        </p>
      )}

      {recording.error && (
        <p className="mt-2 text-sm text-red-700">{recording.error}</p>
      )}
    </div>
  )
}

export function InterviewMediaPanel({ sessionId }: { sessionId: string }) {
  const [media, setMedia] = useState<InterviewMedia | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchInterviewMedia(sessionId)
      .then((value) => {
        if (!cancelled) setMedia(value)
      })
      .catch(() => {
        if (!cancelled) setError('The recording and transcript could not be loaded.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  if (loading) return <p className="text-ink-500 text-sm">Loading interview media…</p>
  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (media === null) return null

  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-ink-900 text-lg font-semibold">Recording</h2>
        <div className="mt-3">
          <RecordingSection recording={media.recording} />
        </div>
      </section>

      <section>
        <h2 className="text-ink-900 text-lg font-semibold">Transcript</h2>
        {media.transcript.length === 0 ? (
          <p className="text-ink-500 mt-3 text-sm">
            No transcript was captured for this interview.
          </p>
        ) : (
          <ol className="mt-3 space-y-3">
            {media.transcript.map((segment) => (
              <li key={segment.sequence} className="flex gap-3 text-sm">
                <span
                  className={
                    segment.speaker === 'interviewer'
                      ? 'text-brand-700 w-24 shrink-0 font-medium'
                      : 'text-ink-900 w-24 shrink-0 font-medium'
                  }
                >
                  {segment.speaker === 'interviewer' ? 'Interviewer' : 'Candidate'}
                </span>
                <span className="text-ink-700 leading-6">{segment.content}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}
