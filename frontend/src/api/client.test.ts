import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  request,
  setStoredToken,
  setUnauthorizedHandler,
} from './client'

function mockFetch(response: Partial<Response> & { jsonBody?: unknown }) {
  const { jsonBody, ...rest } = response
  const body = jsonBody === undefined ? '' : JSON.stringify(jsonBody)
  const fetchMock = vi.fn().mockResolvedValue({
    ok: rest.ok ?? true,
    status: rest.status ?? 200,
    text: () => Promise.resolve(body),
  } as Response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('api client', () => {
  beforeEach(() => {
    setUnauthorizedHandler(null)
    setStoredToken(null)
  })

  it('sends no Authorization header when signed out', async () => {
    const fetchMock = mockFetch({ jsonBody: { ok: true } })
    await request('/api/v1/positions')

    const headers = fetchMock.mock.calls[0]![1].headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
  })

  it('attaches the stored bearer token', async () => {
    setStoredToken('test-token-123')
    const fetchMock = mockFetch({ jsonBody: [] })
    await request('/api/v1/positions')

    const headers = fetchMock.mock.calls[0]![1].headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer test-token-123')
  })

  it('serialises query params and skips undefined values', async () => {
    const fetchMock = mockFetch({ jsonBody: [] })
    await request('/api/v1/positions', { query: { status: 'active', missing: undefined } })

    const url = fetchMock.mock.calls[0]![0] as string
    expect(url).toBe('/api/v1/positions?status=active')
  })

  it('sends form-encoded bodies for the login endpoint shape', async () => {
    const fetchMock = mockFetch({ jsonBody: { access_token: 'x', token_type: 'bearer' } })
    await request('/api/v1/auth/login', {
      method: 'POST',
      form: { username: 'a@b.com', password: 'secret-value' },
    })

    const init = fetchMock.mock.calls[0]![1]
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/x-www-form-urlencoded',
    )
    expect(init.body).toBe('username=a%40b.com&password=secret-value')
  })

  it('returns undefined for a 204 response without parsing a body', async () => {
    mockFetch({ status: 204 })
    await expect(request('/api/v1/positions/1', { method: 'DELETE' })).resolves.toBeUndefined()
  })

  it('throws ApiError carrying the backend detail string', async () => {
    mockFetch({ ok: false, status: 403, jsonBody: { detail: 'You do not have access' } })

    await expect(request('/api/v1/positions/1')).rejects.toMatchObject({
      status: 403,
      detail: 'You do not have access',
    })
  })

  it('flattens FastAPI 422 validation arrays into one message', async () => {
    mockFetch({
      ok: false,
      status: 422,
      jsonBody: {
        detail: [
          { loc: ['body', 'title'], msg: 'must not be blank' },
          { loc: ['body', 'category'], msg: 'field required' },
        ],
      },
    })

    await expect(request('/api/v1/positions', { method: 'POST', json: {} })).rejects.toMatchObject({
      status: 422,
      detail: 'title: must not be blank; category: field required',
    })
  })

  it('notifies the unauthorized handler on a 401 so the session can be cleared', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch({ ok: false, status: 401, jsonBody: { detail: 'Could not validate credentials' } })

    await expect(request('/api/v1/positions')).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('reports an unreachable server as a friendly network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(request('/api/v1/positions')).rejects.toMatchObject({
      status: 0,
      detail: 'Could not reach the server. Check that the API is running.',
    })
  })
})
