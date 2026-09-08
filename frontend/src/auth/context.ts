import { createContext } from 'react'

export interface AuthContextValue {
  token: string | null
  email: string | null
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string) => Promise<void>
  logout: () => void
}

/** Kept in its own module so AuthProvider.tsx exports only a component
 *  (which is what keeps React Fast Refresh working). */
export const AuthContext = createContext<AuthContextValue | null>(null)
