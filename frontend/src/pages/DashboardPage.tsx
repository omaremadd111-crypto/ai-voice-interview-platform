import { Link } from 'react-router-dom'

import { listAllCandidates } from '@/api/candidates'
import { listPositions } from '@/api/positions'
import type { CandidateWithPosition } from '@/api/candidates'
import type { CandidateStatus, Position } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { Avatar } from '@/components/ui/Avatar'
import { Button } from '@/components/ui/Button'
import { CandidateStatusBadge, PositionStatusBadge } from '@/components/ui/Badge'
import { Card, CardHeader } from '@/components/ui/Card'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/Feedback'
import { ArrowRightIcon, BriefcaseIcon, PlusIcon, UsersIcon } from '@/components/ui/Icons'
import { cn } from '@/lib/cn'
import { CANDIDATE_STATUS_LABEL } from '@/lib/labels'
import { useAsync } from '@/lib/useAsync'

interface DashboardData {
  positions: Position[]
  candidates: CandidateWithPosition[]
}

interface AttentionItem {
  key: string
  to: string
  icon: React.ReactNode
  text: string
}

// Same five statuses CandidateStatusBadge already uses, ordered as a pipeline
// left-to-right instead of the enum's declaration order, and tinted from the
// same tone ramp so the breakdown bar below reads as one system with the
// status badges elsewhere rather than a second, competing palette.
const PIPELINE_STATUSES: { status: CandidateStatus; dot: string; bar: string }[] = [
  { status: 'new', dot: 'bg-info-500', bar: 'bg-info-400' },
  { status: 'queued', dot: 'bg-ink-400', bar: 'bg-ink-300' },
  { status: 'screening_in_progress', dot: 'bg-brand-500', bar: 'bg-brand-400' },
  { status: 'screened', dot: 'bg-success-500', bar: 'bg-success-400' },
  { status: 'withdrawn', dot: 'bg-ink-300', bar: 'bg-ink-200' },
]

function MetricTile({
  label,
  value,
  sublabel,
  icon,
  to,
}: {
  label: string
  value: number
  sublabel?: string
  icon: React.ReactNode
  to: string
}) {
  return (
    <Link
      to={to}
      className="card hover:border-brand-300 hover:shadow-md group flex items-start gap-4 p-5 transition-all"
    >
      <span className="bg-brand-50 text-brand-600 group-hover:bg-brand-100 transition-standard rounded-xl p-2.5 transition-colors">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-ink-500 text-sm font-medium">{label}</div>
        <div className="text-ink-900 mt-1 text-3xl font-semibold tabular-nums">{value}</div>
        {sublabel && <div className="text-ink-400 mt-0.5 text-xs">{sublabel}</div>}
      </div>
    </Link>
  )
}

export function DashboardPage() {
  const { data, loading, error, reload } = useAsync<DashboardData>(async () => {
    const [positions, candidates] = await Promise.all([listPositions(), listAllCandidates()])
    return { positions, candidates }
  }, [])

  // The header stays visible in every state below (loading/error included) --
  // losing page chrome on a failed load, with no way back to a retry short of
  // a manual browser refresh, was the actual problem being fixed here.
  const header = (
    <PageHeader title="Dashboard" description="Overview of your open roles and candidate pipeline." />
  )

  if (loading) {
    return (
      <>
        {header}
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="card space-y-3 p-5">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-8 w-1/3" />
              </div>
            ))}
          </div>
          <div className="card space-y-4 p-5">
            <Skeleton className="h-4 w-1/4" />
            <Skeleton className="h-2.5 w-full rounded-full" />
          </div>
        </div>
      </>
    )
  }
  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={reload} />
      </>
    )
  }
  if (!data) return null

  const { positions, candidates } = data
  const activePositions = positions.filter((p) => p.status === 'active')
  const draftPositions = positions.filter((p) => p.status === 'draft')
  const newCandidates = candidates.filter((c) => c.candidate.status === 'new')
  const inProgressCandidates = candidates.filter(
    (c) => c.candidate.status === 'screening_in_progress',
  )
  const screenedCandidates = candidates.filter((c) => c.candidate.status === 'screened')
  const recentCandidates = [...candidates].sort((a, b) => b.candidate.id - a.candidate.id).slice(0, 5)

  const isEmpty = positions.length === 0
  const pipelineTotal = candidates.length
  const statusCounts = PIPELINE_STATUSES.map(({ status, dot, bar }) => ({
    status,
    dot,
    bar,
    label: CANDIDATE_STATUS_LABEL[status],
    count: candidates.filter((c) => c.candidate.status === status).length,
  }))

  // Two currently-known, low-risk categories of "something's waiting on you" --
  // both filtered client-side from data already on the page, not a new
  // endpoint. Only rendered when non-empty, so a clean pipeline shows nothing.
  const needsAttention: AttentionItem[] = []
  if (newCandidates.length > 0) {
    needsAttention.push({
      key: 'new-candidates',
      to: '/candidates',
      icon: <UsersIcon className="h-4 w-4" />,
      text: `${newCandidates.length} new ${newCandidates.length === 1 ? 'candidate' : 'candidates'} not yet queued`,
    })
  }
  if (draftPositions.length > 0) {
    needsAttention.push({
      key: 'draft-positions',
      to: '/positions',
      icon: <BriefcaseIcon className="h-4 w-4" />,
      text: `${draftPositions.length} draft ${draftPositions.length === 1 ? 'position' : 'positions'} not yet active`,
    })
  }

  return (
    <>
      {header}

      {isEmpty ? (
        <div className="card">
          <EmptyState
            icon={<BriefcaseIcon className="h-10 w-10" />}
            title="Start by creating a position"
            description="A position holds the role details and the interview questions the AI agent will ask. Candidates are then added to a position."
            action={
              <Link to="/positions">
                <Button>
                  <PlusIcon className="h-4 w-4" />
                  Create a position
                </Button>
              </Link>
            }
          />
        </div>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <MetricTile
              label="Open positions"
              value={activePositions.length}
              sublabel={`${positions.length} total`}
              to="/positions"
              icon={<BriefcaseIcon className="h-5 w-5" />}
            />
            <MetricTile
              label="Candidates in pipeline"
              value={pipelineTotal}
              sublabel={`${inProgressCandidates.length} screening now`}
              to="/candidates"
              icon={<UsersIcon className="h-5 w-5" />}
            />
            <MetricTile
              label="Screened"
              value={screenedCandidates.length}
              sublabel={
                pipelineTotal > 0
                  ? `${Math.round((screenedCandidates.length / pipelineTotal) * 100)}% of pipeline`
                  : undefined
              }
              to="/candidates"
              icon={<UsersIcon className="h-5 w-5" />}
            />
          </div>

          {/* Pipeline breakdown -- the same five statuses as CandidateStatusBadge,
              as one proportional bar instead of a table, so the shape of the
              pipeline (where candidates are piling up) is readable at a glance. */}
          {pipelineTotal > 0 && (
            <Card className="p-5">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-ink-900 text-sm font-semibold">Candidate pipeline</h3>
                <span className="text-ink-400 text-xs">{pipelineTotal} total</span>
              </div>

              <div className="border-border-subtle bg-surface-sunken mt-4 flex h-2.5 overflow-hidden rounded-full border">
                {statusCounts
                  .filter((s) => s.count > 0)
                  .map((s) => (
                    <div
                      key={s.status}
                      className={cn(s.bar, 'h-full first:rounded-l-full last:rounded-r-full')}
                      style={{ width: `${(s.count / pipelineTotal) * 100}%` }}
                      title={`${s.label}: ${s.count}`}
                    />
                  ))}
              </div>

              <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
                {statusCounts.map((s) => (
                  <div key={s.status} className="flex items-center gap-1.5">
                    <span className={cn('h-2 w-2 shrink-0 rounded-full', s.dot)} />
                    <span className="text-ink-600 text-xs font-medium">{s.label}</span>
                    <span className="text-ink-400 text-xs tabular-nums">{s.count}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {needsAttention.length > 0 && (
            <Card className="overflow-hidden">
              <CardHeader title="Needs attention" />
              <ul className="divide-border-subtle divide-y">
                {needsAttention.map((item) => (
                  <li key={item.key}>
                    <Link
                      to={item.to}
                      className="hover:bg-ink-50 transition-standard group flex items-center gap-3 px-5 py-3.5 transition-colors"
                    >
                      <span className="bg-warning-50 text-warning-700 rounded-lg p-2">{item.icon}</span>
                      <span className="text-ink-800 flex-1 text-sm font-medium">{item.text}</span>
                      <ArrowRightIcon className="text-ink-300 group-hover:text-ink-500 h-4 w-4 transition-colors" />
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="overflow-hidden">
              <CardHeader
                title="Positions"
                actions={
                  <Link to="/positions" className="text-brand-700 text-sm font-medium hover:underline">
                    View all
                  </Link>
                }
              />
              <ul className="divide-border-subtle divide-y">
                {positions.slice(0, 5).map((position) => (
                  <li key={position.id}>
                    <Link
                      to={`/positions/${position.id}`}
                      className="hover:bg-ink-50 transition-standard flex items-center justify-between gap-3 px-5 py-3.5 transition-colors"
                    >
                      <div className="min-w-0">
                        <div className="text-ink-900 truncate text-sm font-medium">
                          {position.title}
                        </div>
                        <div className="text-ink-500 truncate text-xs">{position.company_name}</div>
                      </div>
                      <PositionStatusBadge status={position.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>

            <Card className="overflow-hidden">
              <CardHeader
                title="Recent candidates"
                actions={
                  <Link to="/candidates" className="text-brand-700 text-sm font-medium hover:underline">
                    View all
                  </Link>
                }
              />
              {recentCandidates.length === 0 ? (
                <EmptyState
                  icon={<UsersIcon className="h-8 w-8" />}
                  title="No candidates yet"
                  description="Add a candidate to one of your positions to get started."
                />
              ) : (
                <ul className="divide-border-subtle divide-y">
                  {recentCandidates.map(({ candidate, position }) => (
                    <li key={candidate.id}>
                      <Link
                        to={`/candidates/${candidate.id}`}
                        className="hover:bg-ink-50 transition-standard flex items-center gap-3 px-5 py-3.5 transition-colors"
                      >
                        <Avatar name={candidate.full_name} size="sm" />
                        <div className="min-w-0 flex-1">
                          <div className="text-ink-900 truncate text-sm font-medium">
                            {candidate.full_name}
                          </div>
                          <div className="text-ink-500 truncate text-xs">{position.title}</div>
                        </div>
                        <CandidateStatusBadge status={candidate.status} />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  )
}
