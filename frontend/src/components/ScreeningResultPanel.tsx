import type { ReactNode } from 'react'

import type { CategoryEvaluation, ScreeningResult } from '@/api/types'
import { ScreeningOutcomeBadge } from '@/components/ui/Badge'
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable'
import { EVALUATION_CATEGORY_LABEL, QUESTION_CATEGORY_LABEL } from '@/lib/labels'

function ResultList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-ink-500 text-sm">{empty}</p>
  return (
    <ul className="text-ink-700 list-disc space-y-1.5 pl-5 text-sm">
      {items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
    </ul>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h4 className="text-ink-900 mb-2 text-sm font-semibold">{title}</h4>
      {children}
    </section>
  )
}

const CATEGORY_COLUMNS: DataTableColumn<CategoryEvaluation>[] = [
  {
    key: 'category',
    header: 'Category',
    className: 'text-ink-900 font-medium',
    render: (category) => EVALUATION_CATEGORY_LABEL[category.category],
  },
  {
    key: 'score',
    header: 'Score',
    className: 'text-ink-700',
    render: (category) => (category.score === null ? '—' : `${category.score}/100`),
  },
  {
    key: 'reasoning',
    header: 'Evidence assessment',
    className: 'text-ink-600',
    render: (category) => category.reasoning,
  },
]

export function ScreeningResultPanel({ result }: { result: ScreeningResult }) {
  const coverage = Math.round(result.evidence_coverage * 100)

  return (
    <section id="screening-report" className="card space-y-6 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-ink-900 text-base font-semibold">HR interview report</h3>
          <p className="text-ink-500 mt-1 text-xs">
            Generated from transcript evidence. The human recruiter remains the final decision maker.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {result.is_mock && (
            <span className="bg-warning-50 text-warning-800 ring-warning-200 rounded-full px-2 py-0.5 text-xs font-medium ring-1">
              Demo / Mock Mode
            </span>
          )}
          <ScreeningOutcomeBadge outcome={result.screening_outcome} />
        </div>
      </div>

      <dl className="grid gap-3 sm:grid-cols-3">
        <div className="bg-ink-50 rounded-lg p-4">
          <dt className="text-ink-500 text-xs font-medium uppercase">Overall score</dt>
          <dd className="text-ink-900 mt-1 text-2xl font-semibold">
            {result.overall_score === null ? 'Not scored' : `${result.overall_score}/100`}
          </dd>
        </div>
        <div className="bg-ink-50 rounded-lg p-4">
          <dt className="text-ink-500 text-xs font-medium uppercase">Evidence coverage</dt>
          <dd className="text-ink-900 mt-1 text-2xl font-semibold">{coverage}%</dd>
        </div>
        <div className="bg-ink-50 rounded-lg p-4">
          <dt className="text-ink-500 text-xs font-medium uppercase">Initial screening</dt>
          <dd className="mt-2"><ScreeningOutcomeBadge outcome={result.screening_outcome} /></dd>
        </div>
      </dl>

      {result.overall_score === null && (
        <p className="border-info-200 bg-info-50 text-info-800 rounded-lg border px-4 py-3 text-sm">
          Evidence was too limited for a reliable overall score. This result requires human review.
        </p>
      )}

      <Section title="AI summary">
        <p className="text-ink-700 text-sm leading-6">{result.ai_summary}</p>
      </Section>

      <div className="grid gap-6 md:grid-cols-2">
        <Section title="Strengths">
          <ResultList items={result.strengths} empty="No evidence-backed strengths identified." />
        </Section>
        <Section title="Areas to validate">
          <ResultList items={result.areas_to_validate} empty="No additional validation areas identified." />
        </Section>
      </div>

      <Section title="Category scores">
        <div className="border-border-default overflow-hidden rounded-lg border">
          <DataTable
            caption="Category scores"
            columns={CATEGORY_COLUMNS}
            rows={result.category_scores}
            rowKey={(category) => category.category}
          />
        </div>
      </Section>

      <Section title="Human follow-up questions">
        <ResultList
          items={result.human_follow_up_questions}
          empty="No additional human follow-up questions were generated."
        />
      </Section>

      <Section title="Full transcript">
        <ol className="space-y-4">
          {result.full_transcript.map((turn, index) => (
            <li key={`${turn.question_id}-${index}`} className="border-border-default rounded-lg border p-4">
              <div className="text-ink-500 mb-2 text-xs font-medium uppercase">
                {QUESTION_CATEGORY_LABEL[turn.category]}{turn.is_follow_up ? ' · Follow-up' : ''}
              </div>
              <p className="text-ink-800 text-sm"><span className="font-semibold">Aimy:</span> {turn.question}</p>
              <p className="text-ink-700 mt-2 text-sm"><span className="font-semibold">Candidate:</span> {turn.answer}</p>
            </li>
          ))}
        </ol>
      </Section>
    </section>
  )
}
