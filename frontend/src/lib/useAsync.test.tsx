import { act, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { useAsync } from './useAsync'

// A real rendered component under real React.StrictMode -- not renderHook()
// with a StrictMode wrapper. That combination was tried first and does NOT
// reproduce React's actual double-invoke-effects behavior in this test
// environment (verified directly: a bare useEffect counter hit 2 calls under
// plain render(<StrictMode>...), but only 1 under
// renderHook(fn, { wrapper: StrictMode })). render() is also the more
// faithful reproduction regardless: frontend/src/main.tsx wraps the entire
// app in <StrictMode>, so this is genuinely how every page mounts.
function Probe({ loader, deps }: { loader: () => Promise<string>; deps: unknown[] }) {
  const state = useAsync(loader, deps)
  return (
    <div>
      <span data-testid="loading">{String(state.loading)}</span>
      <span data-testid="data">{state.data ?? ''}</span>
      <span data-testid="error">{state.error ?? ''}</span>
      <button onClick={state.reload}>reload</button>
    </div>
  )
}

async function waitForSettled() {
  await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
}

describe('useAsync under React 18 StrictMode', () => {
  it('calls the loader exactly once on mount, not twice', async () => {
    const loader = vi.fn().mockResolvedValue('result')

    render(
      <StrictMode>
        <Probe loader={loader} deps={[]} />
      </StrictMode>,
    )
    await waitForSettled()

    expect(loader).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('data').textContent).toBe('result')
    expect(screen.getByTestId('error').textContent).toBe('')
  })

  it('still fetches again when deps genuinely change', async () => {
    const loader = vi.fn().mockResolvedValue('result')

    const { rerender } = render(
      <StrictMode>
        <Probe loader={loader} deps={[1]} />
      </StrictMode>,
    )
    await waitForSettled()
    expect(loader).toHaveBeenCalledTimes(1)

    rerender(
      <StrictMode>
        <Probe loader={loader} deps={[2]} />
      </StrictMode>,
    )
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))
  })

  it('still fetches again on reload() even with unchanged deps', async () => {
    const loader = vi.fn().mockResolvedValue('result')

    render(
      <StrictMode>
        <Probe loader={loader} deps={[]} />
      </StrictMode>,
    )
    await waitForSettled()
    expect(loader).toHaveBeenCalledTimes(1)

    act(() => {
      screen.getByRole('button', { name: 'reload' }).click()
    })
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))
  })

  it('propagates a loader rejection exactly once, not per StrictMode run', async () => {
    const loader = vi.fn().mockRejectedValue(new Error('boom'))

    render(
      <StrictMode>
        <Probe loader={loader} deps={[]} />
      </StrictMode>,
    )
    await waitForSettled()

    expect(loader).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('error').textContent).toBe('boom')
    expect(screen.getByTestId('data').textContent).toBe('')
  })

  it('a later request response is not clobbered by an earlier, slower one', async () => {
    // Unrelated to the dedup fix -- guards the pre-existing stale-response
    // guard so the dedup change could not have weakened it.
    let resolveFirst!: (value: string) => void
    const first = new Promise<string>((resolve) => {
      resolveFirst = resolve
    })
    const loader = vi
      .fn()
      .mockImplementationOnce(() => first)
      .mockImplementationOnce(async () => 'second')

    const { rerender } = render(
      <StrictMode>
        <Probe loader={loader} deps={[1]} />
      </StrictMode>,
    )
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))

    rerender(
      <StrictMode>
        <Probe loader={loader} deps={[2]} />
      </StrictMode>,
    )
    await waitForSettled()
    expect(screen.getByTestId('data').textContent).toBe('second')

    resolveFirst('first')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByTestId('data').textContent).toBe('second')
  })
})
