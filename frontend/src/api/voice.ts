import { request } from './client'
import type {
  ConsentNotice,
  InterviewMedia,
  RecordingConsent,
  VoiceInvitation,
  VoiceRoomToken,
} from './types'

export function createVoiceInvitation(queueId: number, itemId: number): Promise<VoiceInvitation> {
  return request<VoiceInvitation>(`/api/v1/queues/${queueId}/items/${itemId}/voice-invite`, {
    method: 'POST',
  })
}

export function exchangeVoiceInvitation(invitation: string): Promise<VoiceRoomToken> {
  return request<VoiceRoomToken>('/api/v1/voice/token', {
    method: 'POST',
    json: { invitation },
  })
}

/** The recording notice shown before joining. Unauthenticated: the candidate
 *  page has no HR session, and the notice contains no interview data. */
export function fetchConsentNotice(): Promise<ConsentNotice> {
  return request<ConsentNotice>('/api/v1/voice/consent-notice')
}

/** Capture consent. Must be called before exchangeVoiceInvitation when
 *  recording is enabled, or the token endpoint returns 428. */
export function recordRecordingConsent(invitation: string): Promise<RecordingConsent> {
  return request<RecordingConsent>('/api/v1/voice/consent', {
    method: 'POST',
    json: { invitation, accepted: true },
  })
}

/** Recording + speaker-labelled transcript for HR review. */
export function fetchInterviewMedia(sessionId: string): Promise<InterviewMedia> {
  return request<InterviewMedia>(`/api/v1/interviews/${sessionId}/media`)
}
