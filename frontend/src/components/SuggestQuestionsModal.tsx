import { useEffect, useState } from 'react'

import { addQuestion, suggestQuestions } from '@/api/questions'
import type { SuggestedQuestion } from '@/api/types'
import { CategoryBadge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { SelectField } from '@/components/ui/Field'
import { ErrorAlert, LoadingBlock, Spinner } from '@/components/ui/Feedback'
import { SparkIcon } from '@/components/ui/Icons'
import { Modal } from '@/components/ui/Modal'
import { toMessage } from '@/lib/useAsync'

/**
 * AI-assisted question suggestions with mandatory HR review.
 *
 * The backend proposes; nothing is saved until HR picks what to keep and
 * confirms. Each kept suggestion is written through the ordinary add-question
 * endpoint, so saved questions are indistinguishable from hand-written ones and
 * remain fully editable, reorderable, and deletable afterwards.
 */
export function SuggestQuestionsModal({
  open,
  positionId,
  existingQuestionCount,
  onClose,
  onAdded,
}: {
  open: boolean
  positionId: number
  existingQuestionCount: number
  onClose: () => void
  onAdded: () => void
}) {
  const [numQuestions, setNumQuestions] = useState<string>('6')

  const [suggestions, setSuggestions] = useState<SuggestedQuestion[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    // Reset each time the dialog opens so a previous run's proposal is never
    // mistaken for a fresh one.
    setSuggestions(null)
    setSelected(new Set())
    setError(null)
  }, [open, positionId])

  async function handleGenerate() {
    setError(null)
    setLoading(true)
    try {
      const result = await suggestQuestions(positionId, {
        numQuestions: Number(numQuestions),
      })
      setSuggestions(result)
      setSelected(new Set(result.map((s) => s.suggestion_id)))
    } catch (caught) {
      setError(toMessage(caught, 'Could not generate suggestions.'))
    } finally {
      setLoading(false)
    }
  }

  function toggle(suggestionId: string) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(suggestionId)) next.delete(suggestionId)
      else next.add(suggestionId)
      return next
    })
  }

  async function handleAddSelected() {
    if (suggestions === null) return
    const keep = suggestions.filter((s) => selected.has(s.suggestion_id))
    if (keep.length === 0) {
      setError('Select at least one question to add.')
      return
    }
    setError(null)
    setSaving(true)
    try {
      // Sequential on purpose: order_index is unique per position, so writing
      // them one at a time keeps the intended ordering deterministic.
      let order = existingQuestionCount
      for (const suggestion of keep) {
        await addQuestion(positionId, {
          category: suggestion.category,
          question: suggestion.question,
          order,
          purpose: suggestion.purpose,
          expected_topics: suggestion.expected_topics,
          difficulty: suggestion.difficulty,
          follow_up_allowed: suggestion.follow_up_allowed,
        })
        order += 1
      }
      onAdded()
      onClose()
    } catch (caught) {
      setError(toMessage(caught, 'Could not add the selected questions.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      title="Suggest questions with AI"
      description="Generate reusable role questions from the Position and Job Description. Review every suggestion before adding it."
      onClose={onClose}
    >
      <div className="space-y-4">
        {error && <ErrorAlert message={error} />}

        <div className="border-brand-100 bg-brand-50 rounded-lg border px-4 py-3">
          <p className="text-brand-900 text-sm font-medium">Job description only</p>
          <p className="text-brand-800 mt-1 text-xs leading-5">
            Only the role title, Job Description, and experience level are used. Candidate
            profiles and CVs are never included in Position Question Bank generation.
          </p>
        </div>

        <div className="max-w-xs">
          <SelectField
            label="How many"
            value={numQuestions}
            onChange={(e) => setNumQuestions(e.target.value)}
          >
            {[4, 6, 8, 10, 12].map((count) => (
              <option key={count} value={count}>
                {count} questions
              </option>
            ))}
          </SelectField>
        </div>

        <Button type="button" onClick={handleGenerate} disabled={loading}>
          {loading ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : <SparkIcon className="h-4 w-4" />}
          {loading ? 'Generating…' : suggestions ? 'Regenerate' : 'Generate suggestions'}
        </Button>

        {loading && <LoadingBlock label="Analysing the role and Job Description…" />}

        {suggestions !== null && !loading && (
          <div className="border-ink-200 space-y-3 border-t pt-4">
            <div className="flex items-center justify-between">
              <p className="text-ink-600 text-sm">
                {selected.size} of {suggestions.length} selected
              </p>
              <button
                type="button"
                className="text-brand-700 text-sm font-medium hover:underline"
                onClick={() =>
                  setSelected(
                    selected.size === suggestions.length
                      ? new Set()
                      : new Set(suggestions.map((s) => s.suggestion_id)),
                  )
                }
              >
                {selected.size === suggestions.length ? 'Clear all' : 'Select all'}
              </button>
            </div>

            <ul className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {suggestions.map((suggestion) => (
                <li key={suggestion.suggestion_id}>
                  <label className="border-ink-200 hover:border-brand-300 flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors">
                    <input
                      type="checkbox"
                      checked={selected.has(suggestion.suggestion_id)}
                      onChange={() => toggle(suggestion.suggestion_id)}
                      className="border-ink-300 text-brand-600 mt-0.5 h-4 w-4 shrink-0 rounded"
                    />
                    <div className="min-w-0">
                      <p className="text-ink-900 text-sm">{suggestion.question}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-2">
                        <CategoryBadge category={suggestion.category} />
                        <span className="text-ink-500 text-xs capitalize">
                          {suggestion.difficulty}
                        </span>
                      </div>
                    </div>
                  </label>
                </li>
              ))}
            </ul>

            <p className="text-ink-500 text-xs">
              Added questions can still be edited, reordered, or deleted like any other.
            </p>

            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button type="button" onClick={handleAddSelected} disabled={saving}>
                {saving && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
                Add {selected.size} question{selected.size === 1 ? '' : 's'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
