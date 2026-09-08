import { PageHeader } from '@/components/layout/PageHeader'
import { ClockIcon } from '@/components/ui/Icons'

/**
 * Placeholder for capabilities that are planned but deliberately not built yet.
 * Shows no fabricated data — it only states that the area is not available.
 */
export function ComingSoonPage({
  title,
  description,
  planned,
}: {
  title: string
  description: string
  planned: string[]
}) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <div className="card px-6 py-14 text-center">
        <div className="bg-ink-100 text-ink-500 mx-auto mb-4 w-fit rounded-xl p-3">
          <ClockIcon className="h-7 w-7" />
        </div>
        <h3 className="text-ink-900 text-base font-semibold">Coming soon</h3>
        <p className="text-ink-500 mx-auto mt-1 max-w-md text-sm">
          This area is not available yet. It is planned for a later release.
        </p>
        <ul className="text-ink-600 mx-auto mt-6 max-w-sm space-y-2 text-left text-sm">
          {planned.map((item) => (
            <li key={item} className="flex gap-2">
              <span className="bg-ink-300 mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
