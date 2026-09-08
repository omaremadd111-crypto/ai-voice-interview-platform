import { useState, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'

import { applyToJob, getPublicJob } from '@/api/public'
import type { ApplyResult, PublicJob } from '@/api/types'
import { ApplicationSubmittedPage } from '@/pages/ApplicationSubmittedPage'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { CvFilePicker } from '@/components/CvFilePicker'
import { TextField } from '@/components/ui/Field'
import { ErrorAlert, LoadingBlock } from '@/components/ui/Feedback'
import { toMessage, useAsync } from '@/lib/useAsync'

/**
 * The public job posting + application page (/jobs/:slug). No authentication,
 * no recruiter data of any kind -- see application/public_job_service.py for
 * exactly what this is allowed to show, and application/application_pipeline_service.py
 * for what applying actually does.
 */
export function JobApplicationPage() {
  const { slug } = useParams<{ slug: string }>()
  const job = useAsync<PublicJob>(() => getPublicJob(slug ?? ''), [slug])

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [cvFile, setCvFile] = useState<File | null>(null)
  const [consent, setConsent] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<ApplyResult | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!slug) return
    setSubmitError(null)
    if (!consent) {
      setSubmitError('Please accept the notice above to apply.')
      return
    }
    setSubmitting(true)
    try {
      const outcome = await applyToJob(slug, {
        fullName,
        email,
        phone: phone.trim() === '' ? undefined : phone,
        consent,
        cvFile,
      })
      setResult(outcome)
    } catch (caught) {
      setSubmitError(toMessage(caught, 'Could not submit your application. Please try again.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (result !== null && job.data !== null) {
    return (
      <ApplicationSubmittedPage
        companyName={job.data.company_name}
        positionTitle={job.data.title}
        interviewToken={result.interview_token}
      />
    )
  }

  return (
    <div className="bg-surface-sunken min-h-screen">
      <div className="mx-auto max-w-2xl px-5 py-10 sm:py-16">
        {job.loading && (
          <div className="card p-8">
            <LoadingBlock label="Loading this job posting…" />
          </div>
        )}

        {!job.loading && (job.error !== null || job.data === null) && (
          <Card className="p-8 text-center">
            <h1 className="text-ink-900 text-lg font-semibold">This job posting isn’t available</h1>
            <p className="text-ink-500 mt-2 text-sm">
              The link may be out of date, or this position may not be open for applications right
              now.
            </p>
          </Card>
        )}

        {!job.loading && job.data !== null && (
          <>
            <Card className="overflow-hidden p-0">
              <div className="bg-ink-950 px-6 py-8 text-white sm:px-10">
                <p className="text-brand-300 text-xs font-semibold tracking-[0.18em] uppercase">
                  {job.data.company_name}
                </p>
                <h1 className="mt-2 text-2xl font-semibold text-balance sm:text-3xl">
                  {job.data.title}
                </h1>
                {job.data.experience_level && (
                  <p className="mt-2 text-sm text-slate-300">{job.data.experience_level}</p>
                )}
              </div>

              {job.data.description && (
                <div className="px-6 py-7 sm:px-10">
                  <h2 className="text-ink-900 text-sm font-semibold">About this role</h2>
                  <p className="text-ink-700 mt-3 text-sm leading-6 whitespace-pre-wrap">
                    {job.data.description}
                  </p>
                </div>
              )}
            </Card>

            <Card className="mt-6 p-6 sm:p-8">
              <h2 className="text-ink-900 text-base font-semibold">Apply for this role</h2>
              <form onSubmit={handleSubmit} className="mt-5 space-y-4" noValidate>
                {submitError && <ErrorAlert message={submitError} />}
                <TextField
                  label="Full name"
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  disabled={submitting}
                />
                <TextField
                  label="Email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                />
                <TextField
                  label="Phone"
                  type="tel"
                  required={job.data.require_phone}
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  disabled={submitting}
                  hint={job.data.require_phone ? undefined : 'Optional.'}
                />
                <CvFilePicker label="CV (optional)" file={cvFile} onSelect={setCvFile} disabled={submitting} />

                {job.data.application_notice && (
                  <p className="text-ink-500 bg-surface-sunken rounded-lg px-3 py-2.5 text-xs leading-5">
                    {job.data.application_notice}
                  </p>
                )}

                <label className="flex items-start gap-2.5 text-sm">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    disabled={submitting}
                    className="border-ink-300 text-brand-600 mt-0.5 h-4 w-4 rounded"
                  />
                  <span className="text-ink-700">
                    I have read the notice above and consent to my information being used to screen
                    my application, including an AI-conducted, recorded voice interview if I proceed.
                  </span>
                </label>

                <div className="pt-2">
                  <Button type="submit" className="w-full" disabled={submitting} loading={submitting}>
                    Submit application
                  </Button>
                </div>
              </form>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
