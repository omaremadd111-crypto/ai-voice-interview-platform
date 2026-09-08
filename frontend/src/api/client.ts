/**
 * The single HTTP boundary for the whole frontend.
 *
 * Every request to the FastAPI backend goes through `request()`. Nothing else in
 * the app calls `fetch` directly, so auth-header injection, error shaping, and
 * the 401 -> logout path all exist in exactly one place.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

const TOKEN_STORAGE_KEY = 'hr_agent_access_token'

/** Notified when the API rejects our token, so AuthProvider can clear session state. */
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    // Private-mode / disabled storage: treat as logged out rather than crashing.
    return null
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(TOKEN_STORAGE_KEY)
    else localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // Ignore: an unwritable store only costs the user session persistence.
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * FastAPI returns `{"detail": ...}` where detail is a string for our own handlers
 * (api/errors.py) but an array of field errors for a 422 schema-validation
 * failure. Flatten both into one human-readable message.
 */
function extractDetail(status: number, body: unknown): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (typeof item !== 'object' || item === null) return null
          const record = item as { loc?: unknown; msg?: unknown }
          const msg = typeof record.msg === 'string' ? record.msg : null
          if (msg === null) return null
          const loc = Array.isArray(record.loc) ? record.loc.filter((p) => p !== 'body') : []
          return loc.length > 0 ? `${loc.join('.')}: ${msg}` : msg
        })
        .filter((message): message is string => message !== null)
      if (messages.length > 0) return messages.join('; ')
    }
  }
  return `Request failed with status ${status}`
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  /** Sent as a JSON body. */
  json?: unknown
  /** Sent as application/x-www-form-urlencoded (the login endpoint expects this). */
  form?: Record<string, string>
  /** Sent as multipart/form-data (file uploads). The browser sets the boundary,
   *  so we must NOT set Content-Type ourselves. */
  formData?: FormData
  /** Appended as a query string; undefined/null values are skipped. */
  query?: Record<string, string | number | undefined | null>
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', json, form, formData, query } = options

  let url = `${API_BASE_URL}${path}`
  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) params.set(key, String(value))
    }
    const queryString = params.toString()
    if (queryString) url += `?${queryString}`
  }

  const headers: Record<string, string> = {}
  const token = getStoredToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let body: string | FormData | undefined
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  } else if (form !== undefined) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    body = new URLSearchParams(form).toString()
  } else if (formData !== undefined) {
    // No Content-Type header on purpose -- fetch adds it with the multipart
    // boundary, and setting it by hand produces a body the server cannot parse.
    body = formData
  }

  let response: Response
  try {
    response = await fetch(url, { method, headers, body })
  } catch {
    // Network-level failure: fetch only rejects when the request never completed.
    throw new ApiError(0, 'Could not reach the server. Check that the API is running.')
  }

  if (response.status === 401) {
    onUnauthorized?.()
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  let parsed: unknown = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(response.status, parsed))
  }

  return parsed as T
}
