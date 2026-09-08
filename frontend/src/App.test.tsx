import { screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as candidatesApi from '@/api/candidates'
import * as positionsApi from '@/api/positions'
import * as queuesApi from '@/api/queues'
import { setStoredToken } from '@/api/client'
import { App } from './App'

function renderApp(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App routing', () => {
  it('redirects an unauthenticated visitor to the sign-in screen', async () => {
    renderApp('/positions')
    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('renders the dashboard shell once a token is present', async () => {
    setStoredToken('a-test-token')
    vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([])
    vi.spyOn(candidatesApi, 'listAllCandidates').mockResolvedValue([])

    renderApp('/')

    expect(await screen.findByRole('navigation', { name: /main/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /positions/i })).toBeInTheDocument()
  })

  it('routes /queues to the real queues page now that it is built', async () => {
    setStoredToken('a-test-token')
    vi.spyOn(positionsApi, 'listPositions').mockResolvedValue([])
    vi.spyOn(queuesApi, 'listAllQueues').mockResolvedValue([])

    renderApp('/queues')

    // level 2 is the page heading; the layout header renders its own h1 with the
    // same text, so the level disambiguates them.
    expect(await screen.findByRole('heading', { name: /^queues$/i, level: 2 })).toBeInTheDocument()
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument()
  })

  it('shows the Knowledge placeholder', async () => {
    setStoredToken('a-test-token')

    renderApp('/knowledge')
    expect(await screen.findByText(/coming soon/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /^knowledge$/i, level: 2 })).toBeInTheDocument()
  })
})
