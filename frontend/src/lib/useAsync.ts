import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '@/api/client'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
  /** Replace the loaded value locally, for when a mutation already returned the
   *  updated record and a refetch would be a wasted round trip. */
  setData: (value: T) => void
}

export function toMessage(caught: unknown, fallback = 'Something went wrong.'): string {
  if (caught instanceof ApiError) return caught.detail
  if (caught instanceof Error && caught.message) return caught.message
  return fallback
}

/**
 * Runs `loader` on mount and whenever `deps` change, tracking loading/error state.
 * Results from a superseded run are discarded so a slow earlier request can never
 * overwrite a newer one.
 *
 * React 18 StrictMode (dev only) intentionally mounts every component twice --
 * effect, cleanup, effect again, synchronously, before either run's promise can
 * have settled -- specifically to surface effects that are not safe to run
 * twice. Without `inFlightRef` below, this hook was not: cleanup only set a
 * local flag to discard a stale RESULT, it never stopped the SECOND call from
 * happening, so every `useAsync`-backed page issued its request twice on first
 * load (visible in the browser Network tab, not just in React's own devtools).
 * `inFlightRef` recognizes "these are the same logical request" by `deps` +
 * `reloadToken` (exactly what the effect already keys on) and reuses the
 * in-flight promise instead of calling `loader` again, so the second
 * synchronous run costs a cache check, not a second network request. A real
 * subsequent fetch -- deps actually changing, or `reload()` -- gets a new key
 * and is never deduplicated against.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const reload = useCallback(() => setReloadToken((token) => token + 1), [])

  const inFlightRef = useRef<{ key: string; promise: Promise<T> } | null>(null)

  useEffect(() => {
    let active = true
    const key = JSON.stringify([...deps, reloadToken])

    setLoading(true)
    setError(null)

    const promise =
      inFlightRef.current !== null && inFlightRef.current.key === key
        ? inFlightRef.current.promise
        : loader()
    inFlightRef.current = { key, promise }

    promise
      .then((result) => {
        if (active) setData(result)
      })
      .catch((caught) => {
        if (active) setError(toMessage(caught))
      })
      .finally(() => {
        if (active) setLoading(false)
        // Only the run that owns the current entry clears it, so a second,
        // genuinely new request already in flight is never clobbered.
        if (inFlightRef.current !== null && inFlightRef.current.key === key) {
          inFlightRef.current = null
        }
      })
    return () => {
      active = false
    }
    // `loader` is intentionally excluded: callers pass an inline closure, which
    // would be a new reference every render. `deps` is the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken])

  return { data, loading, error, reload, setData }
}
