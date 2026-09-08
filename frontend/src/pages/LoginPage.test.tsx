import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { getStoredToken } from '@/api/client'
import { renderWithProviders } from '@/test/renderWithProviders'
import { LoginPage } from './LoginPage'

describe('LoginPage', () => {
  it('stores the returned token after a successful sign in', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue({
      access_token: 'issued-token',
      token_type: 'bearer',
    })
    renderWithProviders(<LoginPage />)

    await userEvent.type(screen.getByLabelText(/work email/i), 'hr@acme.example')
    await userEvent.type(screen.getByLabelText(/password/i), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(getStoredToken()).toBe('issued-token'))
    expect(authApi.login).toHaveBeenCalledWith('hr@acme.example', 'correct horse battery')
  })

  it('surfaces the backend message and stores no token when credentials are rejected', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError(401, 'Invalid email or password'))
    renderWithProviders(<LoginPage />)

    await userEvent.type(screen.getByLabelText(/work email/i), 'hr@acme.example')
    await userEvent.type(screen.getByLabelText(/password/i), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(getStoredToken()).toBeNull()
  })
})
