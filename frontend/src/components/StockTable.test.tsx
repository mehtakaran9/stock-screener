import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockTable from './StockTable'
import type { Stock } from '../types'

const makeStock = (overrides: Partial<Stock> = {}): Stock => ({
  ticker: 'AAPL',
  exchange: 'NASDAQ',
  price: 213.45,
  change: 3.82,
  volume: 52_300_000,
  vol_ratio: 2.3,
  market_cap: 3_290_000_000_000,
  rsi: 61.5,
  macd: 1.23,
  macd_signal: 0.98,
  macd_hist: 0.25,
  ema8: 208.12,
  ema20: 203.50,
  ema50: 195.40,
  ema200: 178.30,
  sma50: 194.80,
  sma200: 185.60,
  bb_upper: 225.10,
  bb_middle: 210.50,
  bb_lower: 195.90,
  atr14: 4.25,
  entry1: 213.45,
  entry2: 208.12,
  entry3: 210.50,
  stop1: 209.20,
  stop2: 205.99,
  stop3: 192.67,
  ...overrides,
})

describe('StockTable — empty state', () => {
  it('renders placeholder row when no stocks', () => {
    render(<StockTable stocks={[]} />)
    expect(screen.getByText(/No stocks found/)).toBeInTheDocument()
  })

  it('renders all column headers in empty state', () => {
    render(<StockTable stocks={[]} />)
    expect(screen.getByText('Ticker')).toBeInTheDocument()
    expect(screen.getByText('Price')).toBeInTheDocument()
  })
})

describe('StockTable — stock rows', () => {
  it('renders a stock row with ticker link', () => {
    render(<StockTable stocks={[makeStock()]} />)
    const link = screen.getByRole('link', { name: 'AAPL' })
    expect(link).toHaveAttribute('href', 'https://www.google.com/finance/quote/AAPL:NASDAQ')
  })

  it('renders price and change', () => {
    render(<StockTable stocks={[makeStock()]} />)
    expect(screen.getByText('$213.45')).toBeInTheDocument()
    expect(screen.getByText(/\+3\.82%/)).toBeInTheDocument()
  })

  it('shows MACD bull indicator when macd_hist > 0', () => {
    render(<StockTable stocks={[makeStock({ macd_hist: 0.25 })]} />)
    expect(screen.getByText('▲ Bull')).toBeInTheDocument()
  })

  it('shows MACD bear indicator when macd_hist < 0', () => {
    render(<StockTable stocks={[makeStock({ macd_hist: -0.1 })]} />)
    expect(screen.getByText('▼ Bear')).toBeInTheDocument()
  })

  it('renders negative change with negative class and minus sign', () => {
    render(<StockTable stocks={[makeStock({ change: -2.5 })]} />)
    expect(screen.getByText(/-2\.50%/)).toBeInTheDocument()
    const cell = screen.getByText(/-2\.50%/).closest('td')!
    expect(cell).toHaveClass('negative')
  })

  it('vol_ratio >= 2 uses success color', () => {
    render(<StockTable stocks={[makeStock({ vol_ratio: 2.5 })]} />)
    const volEl = screen.getByText('2.5× avg')
    expect(volEl).toHaveStyle({ color: 'var(--success)' })
  })

  it('vol_ratio < 2 uses secondary color', () => {
    render(<StockTable stocks={[makeStock({ vol_ratio: 1.5 })]} />)
    const volEl = screen.getByText('1.5× avg')
    expect(volEl).toHaveStyle({ color: 'var(--text-secondary)' })
  })
})

describe('StockTable — RSI coloring', () => {
  it('RSI > 70 renders in danger color', () => {
    render(<StockTable stocks={[makeStock({ rsi: 75 })]} />)
    const badge = screen.getByText('75.0').closest('.rsi-badge')
    expect(badge).toHaveStyle({ color: 'var(--danger)' })
  })

  it('RSI >= 55 renders in success color', () => {
    render(<StockTable stocks={[makeStock({ rsi: 60 })]} />)
    const badge = screen.getByText('60.0').closest('.rsi-badge')
    expect(badge).toHaveStyle({ color: 'var(--success)' })
  })

  it('RSI >= 40 renders in amber color', () => {
    render(<StockTable stocks={[makeStock({ rsi: 45 })]} />)
    const badge = screen.getByText('45.0').closest('.rsi-badge')
    expect(badge).toHaveStyle({ color: '#f59e0b' })
  })

  it('RSI < 40 renders in secondary color', () => {
    render(<StockTable stocks={[makeStock({ rsi: 30 })]} />)
    const badge = screen.getByText('30.0').closest('.rsi-badge')
    expect(badge).toHaveStyle({ color: 'var(--text-secondary)' })
  })
})

describe('StockTable — RSI labels', () => {
  it('RSI > 70 shows OB label', () => {
    render(<StockTable stocks={[makeStock({ rsi: 71 })]} />)
    expect(screen.getByText('OB')).toBeInTheDocument()
  })

  it('RSI >= 55 shows Bull label', () => {
    render(<StockTable stocks={[makeStock({ rsi: 61.5 })]} />)
    expect(screen.getByText('Bull')).toBeInTheDocument()
  })

  it('RSI >= 40 shows Neut label', () => {
    render(<StockTable stocks={[makeStock({ rsi: 45 })]} />)
    expect(screen.getByText('Neut')).toBeInTheDocument()
  })

  it('RSI < 40 shows Weak label', () => {
    render(<StockTable stocks={[makeStock({ rsi: 30 })]} />)
    expect(screen.getByText('Weak')).toBeInTheDocument()
  })
})

describe('StockTable — formatNumber', () => {
  it('formats trillions', () => {
    render(<StockTable stocks={[makeStock({ market_cap: 3_290_000_000_000 })]} />)
    expect(screen.getByText('3.29T')).toBeInTheDocument()
  })

  it('formats billions', () => {
    render(<StockTable stocks={[makeStock({ market_cap: 2_100_000_000 })]} />)
    expect(screen.getByText('2.10B')).toBeInTheDocument()
  })

  it('formats millions for volume', () => {
    render(<StockTable stocks={[makeStock({ volume: 5_200_000, market_cap: 1_200_000_000 })]} />)
    expect(screen.getByText('5.20M')).toBeInTheDocument()
  })

  it('formats thousands', () => {
    render(<StockTable stocks={[makeStock({ volume: 750_000, market_cap: 1_200_000_000 })]} />)
    expect(screen.getByText('750.00K')).toBeInTheDocument()
  })

  it('formats small numbers as-is', () => {
    render(<StockTable stocks={[makeStock({ volume: 500, market_cap: 1_200_000_000 })]} />)
    expect(screen.getByText('500')).toBeInTheDocument()
  })
})

describe('StockTable — sorting', () => {
  const stocks = [
    makeStock({ ticker: 'AAPL', change: 3.0, price: 200 }),
    makeStock({ ticker: 'NVDA', change: 5.0, price: 100 }),
  ]

  it('sorts by change descending by default', () => {
    render(<StockTable stocks={stocks} />)
    const rows = screen.getAllByRole('link').map(l => l.textContent)
    expect(rows[0]).toBe('NVDA')
    expect(rows[1]).toBe('AAPL')
  })

  it('reverses sort direction when same header clicked twice', () => {
    render(<StockTable stocks={stocks} />)
    const changeHeader = screen.getAllByRole('columnheader').find(h => h.textContent?.includes('Change'))!
    fireEvent.click(changeHeader)
    const rows = screen.getAllByRole('link').map(l => l.textContent)
    expect(rows[0]).toBe('AAPL')
  })

  it('switches sort key when a different header is clicked', () => {
    render(<StockTable stocks={stocks} />)
    const priceHeader = screen.getAllByRole('columnheader').find(h => h.textContent?.includes('Price'))!
    fireEvent.click(priceHeader)
    const rows = screen.getAllByRole('link').map(l => l.textContent)
    expect(rows[0]).toBe('AAPL') // price 200 > 100, desc
  })

  it('shows sort-active class on active column', () => {
    render(<StockTable stocks={stocks} />)
    const changeHeader = screen.getAllByRole('columnheader').find(h => h.textContent?.includes('Change'))!
    expect(changeHeader.querySelector('.sort-active')).toBeInTheDocument()
  })

  it('shows ascending arrow after two clicks on same column', async () => {
    render(<StockTable stocks={stocks} />)
    const getChangeHeader = () =>
      screen.getAllByRole('columnheader').find(h => h.textContent?.includes('Change'))!
    fireEvent.click(getChangeHeader()) // desc → asc
    await waitFor(() => expect(getChangeHeader().textContent).toContain('↑'))
  })

  it('returns to descending after three clicks on same column', async () => {
    render(<StockTable stocks={stocks} />)
    const getChangeHeader = () =>
      screen.getAllByRole('columnheader').find(h => h.textContent?.includes('Change'))!
    fireEvent.click(getChangeHeader()) // desc → asc
    fireEvent.click(getChangeHeader()) // asc → desc
    await waitFor(() => expect(getChangeHeader().textContent).toContain('↓'))
    const rows = screen.getAllByRole('link').map(l => l.textContent)
    expect(rows[0]).toBe('NVDA') // back to desc: 5.0 > 3.0
  })
})

describe('StockTable — ticker link', () => {
  it('clicking the ticker link does not expand the row (stopPropagation)', () => {
    render(<StockTable stocks={[makeStock()]} />)
    const link = screen.getByRole('link', { name: 'AAPL' })
    fireEvent.click(link)
    expect(screen.queryByText('Moving Averages')).not.toBeInTheDocument()
  })
})

describe('StockTable — row expansion', () => {
  it('expands row on click to show detail panel', () => {
    render(<StockTable stocks={[makeStock()]} />)
    const row = screen.getByRole('link', { name: 'AAPL' }).closest('tr')!
    fireEvent.click(row)
    expect(screen.getByText('Moving Averages')).toBeInTheDocument()
    expect(screen.getByText('Bollinger Bands (20, 2) & ATR')).toBeInTheDocument()
    expect(screen.getByText('Swing Trade Levels')).toBeInTheDocument()
  })

  it('shows correct entry and stop values in expanded panel', () => {
    render(<StockTable stocks={[makeStock()]} />)
    const row = screen.getByRole('link', { name: 'AAPL' }).closest('tr')!
    fireEvent.click(row)
    expect(screen.getAllByText('$213.45').length).toBeGreaterThan(0)
    expect(screen.getByText('$209.20')).toBeInTheDocument()
  })

  it('collapses row on second click', () => {
    render(<StockTable stocks={[makeStock()]} />)
    const row = screen.getByRole('link', { name: 'AAPL' }).closest('tr')!
    fireEvent.click(row)
    expect(screen.getByText('Moving Averages')).toBeInTheDocument()
    fireEvent.click(row)
    expect(screen.queryByText('Moving Averages')).not.toBeInTheDocument()
  })
})
