import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { CheckIcon } from '@/components/ui/Icons'

/**
 * The confirmation view shown in place of the apply form once
 * JobApplicationPage's submit succeeds. Not a route of its own: the
 * interview_token it needs only ever exists in that one API response (it is
 * never persisted in a form a later page load could recover -- see
 * api/schemas/public.py's ApplyResponse), so there is nothing a standalone
 * URL for this page could usefully load.
 */
export function ApplicationSubmittedPage({
  companyName,
  positionTitle,
  interviewToken,
}: {
  companyName: string
  positionTitle: string
  interviewToken: string | null
}) {
  return (
    <div className="bg-surface-sunken min-h-screen">
      <div className="mx-auto max-w-lg px-5 py-16 sm:py-24">
        <Card className="p-8 text-center sm:p-10">
          <div className="bg-success-50 text-success-600 mx-auto flex h-12 w-12 items-center justify-center rounded-full">
            <CheckIcon className="h-6 w-6" />
          </div>
          <h1 className="text-ink-900 mt-5 text-xl font-semibold">Application submitted</h1>
          <p className="text-ink-500 mt-2 text-sm leading-6">
            Thanks for applying to <span className="text-ink-700 font-medium">{positionTitle}</span>{' '}
            at <span className="text-ink-700 font-medium">{companyName}</span>. Your details have
            been received.
          </p>

          {interviewToken !== null ? (
            <div className="border-border-default mt-7 border-t pt-7">
              <p className="text-ink-700 text-sm">
                Your screening interview is ready whenever you are.
              </p>
              <a href={`/interview/${interviewToken}`} className="mt-4 block">
                <Button className="w-full">Start interview now</Button>
              </a>
              <p className="text-ink-400 mt-3 text-xs">
                Not ready yet? Bookmark this link and come back to it later:
                <br />
                <span className="text-ink-500 break-all">
                  {typeof window !== 'undefined' ? window.location.origin : ''}/interview/
                  {interviewToken}
                </span>
              </p>
            </div>
          ) : (
            <div className="border-border-default mt-7 border-t pt-7">
              <p className="text-ink-500 text-sm">
                The hiring team will review your application and reach out with next steps.
              </p>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
