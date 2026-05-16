import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

// ── Mock EventSource ─────────────────────────────────────────────────────────
class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn()

  constructor(_url: string) {
    MockEventSource.instances.push(this)
  }

  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  emitError() {
    this.onerror?.(new Event('error'))
  }
}

// ── Stock payload helper ─────────────────────────────────────────────────────
const stockPayload = (ticker = 'AAPL') => ({
  status: 'result',
  data: {
    ticker, exchange: 'NASDAQ', price: 213.45, change: 3.82,
    volume: 52_300_000, vol_ratio: 2.3, market_cap: 3_290_000_000_000,
    rsi: 61.5, macd: 1.23, macd_signal: 0.98, macd_hist: 0.25,
    ema8: 208.12, ema50: 195.40, ema200: 178.30, sma50: 194.80, sma200: 185.60,
    bb_upper: 225.10, bb_middle: 210.50, bb_lower: 195.90, atr14: 4.25,
    entry1: 213.45, entry2: 208.12, entry3: 210.50,
    stop1: 209.20, stop2: 205.99, stop3: 192.67,
  },
})

// ── Mock fetch factory ───────────────────────────────────────────────────────
function makeFetch(filters = ['Day Change > 3%']) {
  return vi.fn().mockImplementation((url: string) => {
    if ((url as string).includes('/api/filters')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ filters }) })
    }
    return Promise.resolve({ ok: true })
  })
}

// ── Setup / teardown ─────────────────────────────────────────────────────────
beforeEach(() => {
  MockEventSource.instances = []
  vi.stubGlobal('EventSource', MockEventSource)
  vi.stubGlobal('fetch', makeFetch())
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// Helper — wait for EventSource to be created after mount
async function renderAndWaitForES() {
  render(<App />)
  await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
  return MockEventSource.instances[0]
}

// ── Tests ────────────────────────────────────────────────────────────────────
describe('App — initial render', () => {
  it('renders the app name', () => {
    render(<App />)
    expect(screen.getByText('StockScreener Pro')).toBeInTheDocument()
  })

  it('renders scan control buttons', () => {
    render(<App />)
    // Auto-start ping is in progress, so button shows "Waking up…" before "Scanning…"
    expect(screen.getByRole('button', { name: /waking up|scanning/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reset/i })).toBeInTheDocument()
  })

  it('fetches and displays active filters on mount', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Day Change > 3%')).toBeInTheDocument())
  })

  it('auto-starts scan on mount and creates an EventSource', async () => {
    await renderAndWaitForES()
    expect(MockEventSource.instances).toHaveLength(1)
  })

  it('shows Scanning… and disables button while scanning', async () => {
    await renderAndWaitForES()
    expect(screen.getByRole('button', { name: /scanning/i })).toBeDisabled()
  })
})

describe('App — SSE events', () => {
  it('shows progress bar and ticker on progress event (current > 0)', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'progress', total: 100, current: 25, ticker: 'MSFT' }) })
    expect(screen.getByText(/25 \/ 100/)).toBeInTheDocument()
    expect(screen.getByText(/Scanning: MSFT/)).toBeInTheDocument()
  })

  it('shows Calculating… ETA on progress event with current = 0', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'progress', total: 100, current: 0 }) })
    expect(screen.getByText(/Calculating\.\.\./)).toBeInTheDocument()
  })

  it('shows ETA when progress current > 0', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'progress', total: 100, current: 50, ticker: 'AAPL' }) })
    expect(screen.getByText(/ETA:/)).toBeInTheDocument()
  })

  it('adds a result stock to the table', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit(stockPayload('NVDA')) })
    expect(screen.getByRole('link', { name: 'NVDA' })).toBeInTheDocument()
  })

  it('ignores duplicate result events for the same ticker', async () => {
    const es = await renderAndWaitForES()
    act(() => {
      es.emit(stockPayload('AAPL'))
      es.emit(stockPayload('AAPL'))
    })
    expect(screen.getAllByRole('link', { name: 'AAPL' })).toHaveLength(1)
  })

  it('shows scan-complete message and match count after complete event', async () => {
    const es = await renderAndWaitForES()
    act(() => {
      es.emit(stockPayload('AAPL'))
      es.emit({ status: 'complete', total: 500 })
    })
    await waitFor(() => expect(screen.getByText(/Scan complete/)).toBeInTheDocument())
    expect(screen.getByText(/1 matches found/)).toBeInTheDocument()
  })

  it('re-enables Start Scan button after scan completes', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'complete', total: 500 }) })
    await waitFor(() => expect(screen.getByRole('button', { name: /start scan/i })).not.toBeDisabled())
  })

  it('shows warning banner on warning event', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'warning', message: 'Using fallback tickers.' }) })
    expect(screen.getByText('Using fallback tickers.')).toBeInTheDocument()
  })

  it('stops scanning and re-enables button on SSE error', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emitError() })
    await waitFor(() => expect(screen.getByRole('button', { name: /start scan/i })).not.toBeDisabled())
  })
})

describe('App — error handling', () => {
  it('stops scanning immediately when ping fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network error')))
    render(<App />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /start scan/i })).not.toBeDisabled()
    )
  })

  it('handles filters endpoint failure gracefully without crashing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if ((url as string).includes('/api/filters'))
        return Promise.resolve({ ok: false, status: 500 })
      return Promise.resolve({ ok: true })
    }))
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    expect(console.error).toHaveBeenCalled()
  })
})

describe('App — malformed SSE data', () => {
  it('handles invalid JSON in SSE message without crashing', async () => {
    const es = await renderAndWaitForES()
    act(() => {
      es.onmessage?.({ data: 'not-valid-json' } as MessageEvent)
    })
    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining('Failed to parse'),
      expect.anything()
    )
  })

  it('ignores SSE messages with unknown status without crashing', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'unknown_event' }) })
    expect(screen.queryByText('Moving Averages')).not.toBeInTheDocument()
  })
})

describe('App — startScan guard', () => {
  it('does nothing when startScan is called while already scanning', async () => {
    await renderAndWaitForES()
    // fireEvent bypasses the disabled attribute, triggering startScan while isScanning=true
    const scanningBtn = screen.getByRole('button', { name: /scanning/i })
    fireEvent.click(scanningBtn)
    // Still scanning — button still shows Scanning…
    expect(screen.getByRole('button', { name: /scanning/i })).toBeInTheDocument()
  })
})

describe('App — Reset button', () => {
  it('calls window.location.reload when Reset is clicked', async () => {
    const reload = vi.fn()
    Object.defineProperty(window, 'location', { value: { reload }, configurable: true })
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: /reset/i }))
    expect(reload).toHaveBeenCalledOnce()
  })
})

describe('App — re-scan and warning clear', () => {
  it('clears warning banner when a new scan starts', async () => {
    const es = await renderAndWaitForES()
    act(() => { es.emit({ status: 'warning', message: 'Fallback used.' }) })
    expect(screen.getByText('Fallback used.')).toBeInTheDocument()

    // Complete first scan so button re-enables
    act(() => { es.emit({ status: 'complete', total: 100 }) })
    await waitFor(() => expect(screen.getByRole('button', { name: /start scan/i })).not.toBeDisabled())

    await userEvent.click(screen.getByRole('button', { name: /start scan/i }))
    expect(screen.queryByText('Fallback used.')).not.toBeInTheDocument()
  })

  it('clears previous results when a new scan starts', async () => {
    const es = await renderAndWaitForES()
    act(() => {
      es.emit(stockPayload('AAPL'))
      es.emit({ status: 'complete', total: 100 })
    })
    await waitFor(() => expect(screen.getByRole('button', { name: /start scan/i })).not.toBeDisabled())

    await userEvent.click(screen.getByRole('button', { name: /start scan/i }))
    expect(screen.queryByRole('link', { name: 'AAPL' })).not.toBeInTheDocument()
  })
})
