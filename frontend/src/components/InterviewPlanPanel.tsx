import type { PrepareInterviewResult } from '@/api/types'
import { Badge, CategoryBadge } from '@/components/ui/Badge'

function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <h4 className="text-ink-500 text-xs font-semibold tracking-wide uppercase">{title}</h4>
      <ul className="mt-2 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-ink-700 flex gap-2 text-sm">
            <span className="text-ink-300 mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Renders the interview plan the backend produced for HR review.
 *
 * This is an HR-facing view. The candidate never sees this screen, and the
 * backend response deliberately contains no rubric weights, scoring
 * configuration, or evaluator prompts — only the plan and the analyses.
 */
export function InterviewPlanPanel({ result }: { result: PrepareInterviewResult }) {
  const { interview_plan, job_analysis, candidate_analysis, fit_analysis } = result

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="success">Interview prepared</Badge>
        <Badge tone="neutral">State: {result.state}</Badge>
        {result.is_mock && <Badge tone="warning">Demo / Mock Mode — not a real model response</Badge>}
        <span className="text-ink-500 text-xs">Provider: {result.llm_provider}</span>
      </div>

      <section className="card overflow-hidden">
        <div className="border-ink-200 border-b px-5 py-4">
          <h3 className="text-ink-900 text-sm font-semibold">
            Interview plan · {interview_plan.questions.length} questions
          </h3>
          <p className="text-ink-500 mt-0.5 text-xs">
            The order the agent will follow. HR-only view — never shown to the candidate.
          </p>
        </div>
        <ol className="divide-ink-100 divide-y">
          {interview_plan.questions.map((question, index) => (
            <li key={question.id} className="px-5 py-4">
              <div className="flex items-start gap-3">
                <span className="bg-ink-100 text-ink-600 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold tabular-nums">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-ink-900 text-sm">{question.question}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <CategoryBadge category={question.category} />
                    <span className="text-ink-500 text-xs capitalize">{question.difficulty}</span>
                    {question.follow_up_allowed && (
                      <span className="text-ink-500 text-xs">· follow-ups allowed</span>
                    )}
                  </div>
                  {question.purpose && (
                    <p className="text-ink-500 mt-2 text-xs">Purpose: {question.purpose}</p>
                  )}
                  {question.expected_topics.length > 0 && (
                    <p className="text-ink-500 mt-1 text-xs">
                      Expected topics: {question.expected_topics.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section className="card space-y-4 p-5">
          <h3 className="text-ink-900 text-sm font-semibold">Role analysis</h3>
          {job_analysis.role_summary && (
            <p className="text-ink-700 text-sm">{job_analysis.role_summary}</p>
          )}
          <ListBlock title="Required skills" items={job_analysis.required_skills} />
          <ListBlock title="Technical topics" items={job_analysis.technical_topics} />
          <ListBlock title="Responsibilities" items={job_analysis.responsibilities} />
        </section>

        <section className="card space-y-4 p-5">
          <h3 className="text-ink-900 text-sm font-semibold">Candidate analysis</h3>
          <ListBlock title="Skills" items={candidate_analysis.skills} />
          <ListBlock title="Technologies" items={candidate_analysis.technologies} />
          <ListBlock title="Notable CV claims" items={candidate_analysis.important_cv_claims} />
          <ListBlock
            title="Claims to validate"
            items={candidate_analysis.unclear_claims_to_validate}
          />
        </section>
      </div>

      {(fit_analysis.strong_alignment_areas.length > 0 ||
        fit_analysis.skills_requiring_validation.length > 0 ||
        fit_analysis.questions_to_investigate.length > 0 ||
        fit_analysis.missing_information.length > 0) && (
        <section className="card grid gap-6 p-5 md:grid-cols-2">
          <div className="md:col-span-2">
            <h3 className="text-ink-900 text-sm font-semibold">Candidate ↔ role fit</h3>
            <p className="text-ink-500 mt-0.5 text-xs">
              Areas to explore during the interview. Not a hiring decision.
            </p>
          </div>
          <ListBlock title="Strong alignment" items={fit_analysis.strong_alignment_areas} />
          <ListBlock title="Needs validation" items={fit_analysis.skills_requiring_validation} />
          <ListBlock title="Worth investigating" items={fit_analysis.questions_to_investigate} />
          <ListBlock title="Missing information" items={fit_analysis.missing_information} />
        </section>
      )}
    </div>
  )
}
