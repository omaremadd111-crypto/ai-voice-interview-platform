import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { useAuth } from '@/auth/useAuth'
import { Button } from '@/components/ui/Button'
import { TextField } from '@/components/ui/Field'
import { ErrorAlert, Spinner } from '@/components/ui/Feedback'

export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle: string
  children: React.ReactNode
  footer: React.ReactNode
}) {
  return (
    <div className="from-ink-50 to-brand-50 flex min-h-screen items-center justify-center bg-gradient-to-br px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <div className="bg-brand-600 flex h-10 w-10 items-center justify-center rounded-xl text-lg font-bold text-white">
            T
          </div>
          <div>
            <div className="text-ink-900 font-semibold">Talentlane</div>
            <div className="text-ink-500 text-xs">AI Screening Workspace</div>
          </div>
        </div>
        <div className="card p-7">
          <h1 className="text-ink-900 text-xl font-semibold">{title}</h1>
          <p className="text-ink-500 mt-1 text-sm">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </div>
        <p className="text-ink-500 mt-5 text-center text-sm">{footer}</p>
      </div>
    </div>
  )
}

export function LoginPage() {
  const { login, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (isAuthenticated) return <Navigate to="/" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : 'Sign in failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Access your screening workspace."
      footer={
        <>
          No account yet?{' '}
          <Link to="/register" className="text-brand-700 font-medium hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error && <ErrorAlert message={error} />}
        <TextField
          label="Work email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
        />
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
          {submitting ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </AuthShell>
  )
}
