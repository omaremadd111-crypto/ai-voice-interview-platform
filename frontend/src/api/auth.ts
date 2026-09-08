import { request } from './client'
import type { TokenResponse, User } from './types'

export function register(email: string, password: string, fullName: string): Promise<User> {
  return request<User>('/api/v1/auth/register', {
    method: 'POST',
    json: { email, password, full_name: fullName },
  })
}

export function login(email: string, password: string): Promise<TokenResponse> {
  // The backend uses OAuth2PasswordRequestForm, whose field is named `username`
  // (it carries the email) and which requires a form-encoded body, not JSON.
  return request<TokenResponse>('/api/v1/auth/login', {
    method: 'POST',
    form: { username: email, password },
  })
}
