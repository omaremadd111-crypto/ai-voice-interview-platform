import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import * as authApi from '@/api/auth'
import { getStoredToken, setStoredToken, setUnauthorizedHandler } from '@/api/client'
import { AuthContext, type AuthContextValue } from './context'

const EMAIL_STORAGE_KEY = 'hr_agent_user_email'

function readStoredEmail(): string | null {
  try {
    return localStorage.getItem(EMAIL_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredEmail(email: string | null): void {
  try {
    if (email === null) localStorage.removeItem(EMAIL_STORAGE_KEY)
    else localStorage.setItem(EMAIL_STORAGE_KEY, email)
  } catch {
    // Ignore: only costs display of the signed-in address after a reload.
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken())
  const [email, setEmail] = useState<string | null>(() => readStoredEmail())

  const logout = useCallback(() => {
    setStoredToken(null)
    writeStoredEmail(null)
    setToken(null)
    setEmail(null)
  }, [])

  // The API client owns the 401 detection; this wires it to session state so an
  // expired or revoked token drops the user back to the login screen.
  useEffect(() => {
    setUnauthorizedHandler(logout)
    return () => setUnauthorizedHandler(null)
  }, [logout])

  const login = useCallback(async (userEmail: string, password: string) => {
    const response = await authApi.login(userEmail, password)
    setStoredToken(response.access_token)
    writeStoredEmail(userEmail)
    setToken(response.access_token)
    setEmail(userEmail)
  }, [])

  const register = useCallback(
    async (userEmail: string, password: string, fullName: string) => {
      await authApi.register(userEmail, password, fullName)
      await login(userEmail, password)
    },
    [login],
  )

  const value = useMemo<AuthContextValue>(
    () => ({ token, email, isAuthenticated: token !== null, login, register, logout }),
    [token, email, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
