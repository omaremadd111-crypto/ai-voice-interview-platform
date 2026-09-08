import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from '@/auth/AuthContext'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { AppLayout } from '@/components/layout/AppLayout'
import { AgentConfigPage } from '@/pages/AgentConfigPage'
import { CandidateDetailPage } from '@/pages/CandidateDetailPage'
import { CandidatesPage } from '@/pages/CandidatesPage'
import { ComingSoonPage } from '@/pages/ComingSoonPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { InterviewLandingPage } from '@/pages/InterviewLandingPage'
import { JobApplicationPage } from '@/pages/JobApplicationPage'
import { LoginPage } from '@/pages/LoginPage'
import { PositionDetailPage } from '@/pages/PositionDetailPage'
import { PositionsPage } from '@/pages/PositionsPage'
import { QueueDetailPage } from '@/pages/QueueDetailPage'
import { QueuesPage } from '@/pages/QueuesPage'
import { RegisterPage } from '@/pages/RegisterPage'

const VoiceInterviewPage = lazy(() =>
  import('@/pages/VoiceInterviewPage').then((module) => ({ default: module.VoiceInterviewPage })),
)

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/jobs/:slug" element={<JobApplicationPage />} />
      <Route path="/interview/:token" element={<InterviewLandingPage />} />
      <Route
        path="/voice/:invitation"
        element={
          <Suspense fallback={<main className="p-8 text-center text-sm">Loading interview…</main>}>
            <VoiceInterviewPage />
          </Suspense>
        }
      />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="/positions" element={<PositionsPage />} />
          <Route path="/positions/:positionId" element={<PositionDetailPage />} />
          <Route path="/candidates" element={<CandidatesPage />} />
          <Route path="/candidates/:candidateId" element={<CandidateDetailPage />} />
          <Route path="/agent-config" element={<AgentConfigPage />} />
          <Route path="/queues" element={<QueuesPage />} />
          <Route path="/queues/:queueId" element={<QueueDetailPage />} />
          <Route
            path="/knowledge"
            element={
              <ComingSoonPage
                title="Knowledge"
                description="Reference material the agent can draw on when phrasing questions."
                planned={[
                  'Upload company and role reference documents',
                  'Reuse prior context across a candidate’s interviews',
                  'Input-side only — never used to score a candidate',
                ]}
              />
            }
          />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}
