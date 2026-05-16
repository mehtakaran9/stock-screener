import React, { useState } from 'react';
import { TrendingUp, TrendingDown, BarChart3, ChevronDown, ChevronRight } from 'lucide-react';
import type { Stock } from '../types';

interface StockTableProps {
  stocks: Stock[];
}

type SortKey = 'price' | 'change' | 'volume' | 'vol_ratio' | 'market_cap' | 'rsi' | 'macd_hist';

const formatNumber = (num: number) => {
  if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
  if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
  if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
  if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
  return num.toLocaleString();
};

const getRsiColor = (rsi: number) => {
  if (rsi > 70) return 'var(--danger)';
  if (rsi >= 55) return 'var(--success)';
  if (rsi >= 40) return '#f59e0b';
  return 'var(--text-secondary)';
};

const getRsiLabel = (rsi: number) => {
  if (rsi > 70) return 'OB';
  if (rsi >= 55) return 'Bull';
  if (rsi >= 40) return 'Neut';
  return 'Weak';
};

const StockTable: React.FC<StockTableProps> = ({ stocks }) => {
  const [sortKey, setSortKey] = useState<SortKey>('change');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sorted = [...stocks].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  const toggleExpand = (ticker: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
  };

  const SortTh = ({ label, sortK, align = 'left' }: { label: string; sortK: SortKey; align?: string }) => (
    <th
      onClick={() => toggleSort(sortK)}
      className="sortable-th"
      style={{ textAlign: align as 'left' | 'right' }}
    >
      {label}
      <span className={`sort-arrow ${sortKey === sortK ? 'sort-active' : ''}`}>
        {sortKey === sortK && sortDir === 'asc' ? ' ↑' : ' ↓'}
      </span>
    </th>
  );

  if (stocks.length === 0) {
    return (
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Price</th>
              <th>Change %</th>
              <th>Volume</th>
              <th>Market Cap</th>
              <th>RSI</th>
              <th>MACD</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={7} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
                No stocks found matching criteria. Start a scan to find opportunities.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <SortTh label="Price" sortK="price" />
            <SortTh label="Change %" sortK="change" />
            <SortTh label="Volume" sortK="volume" />
            <SortTh label="Market Cap" sortK="market_cap" />
            <SortTh label="RSI" sortK="rsi" />
            <SortTh label="MACD" sortK="macd_hist" />
          </tr>
        </thead>
        <tbody>
          {sorted.map(stock => {
            const isExpanded = expanded.has(stock.ticker);
            const risk1 = (stock.entry1 - stock.stop1).toFixed(2);
            const risk2 = (stock.entry2 - stock.stop2).toFixed(2);
            const risk3 = (stock.entry3 - stock.stop3).toFixed(2);
            const macdBull = stock.macd_hist >= 0;

            return (
              <React.Fragment key={stock.ticker}>
                <tr
                  onClick={() => toggleExpand(stock.ticker)}
                  className={isExpanded ? 'selected' : ''}
                >
                  <td className="ticker-cell">
                    <div className="flex-center" style={{ gap: '6px' }}>
                      {isExpanded
                        ? <ChevronDown size={13} style={{ flexShrink: 0, opacity: 0.6 }} />
                        : <ChevronRight size={13} style={{ flexShrink: 0, opacity: 0.4 }} />}
                      <a
                        href={`https://www.google.com/finance/quote/${stock.ticker}:${stock.exchange}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={e => e.stopPropagation()}
                      >
                        {stock.ticker}
                      </a>
                    </div>
                  </td>
                  <td>${stock.price.toFixed(2)}</td>
                  <td className={stock.change >= 0 ? 'positive' : 'negative'}>
                    <div className="flex-center">
                      {stock.change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                      {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)}%
                    </div>
                  </td>
                  <td>
                    <div className="flex-center">
                      <BarChart3 size={13} style={{ marginRight: '4px', opacity: 0.6 }} />
                      {formatNumber(stock.volume)}
                    </div>
                    <div className="vol-ratio" style={{ color: stock.vol_ratio >= 2 ? 'var(--success)' : 'var(--text-secondary)' }}>
                      {stock.vol_ratio.toFixed(1)}× avg
                    </div>
                  </td>
                  <td>{formatNumber(stock.market_cap)}</td>
                  <td>
                    <div className="rsi-badge" style={{ color: getRsiColor(stock.rsi) }}>
                      <span className="rsi-value">{stock.rsi.toFixed(1)}</span>
                      <span className="rsi-label">{getRsiLabel(stock.rsi)}</span>
                    </div>
                  </td>
                  <td>
                    <span className={macdBull ? 'positive' : 'negative'} style={{ fontWeight: 600, fontSize: '0.8rem' }}>
                      {macdBull ? '▲ Bull' : '▼ Bear'}
                    </span>
                  </td>
                </tr>

                {isExpanded && (
                  <tr className="expanded-row">
                    <td colSpan={7}>
                      <div className="expand-panel">

                        <div className="expand-section">
                          <div className="expand-label">Moving Averages</div>
                          <div className="ma-grid">
                            {[
                              ['EMA 8', stock.ema8],
                              ['EMA 50', stock.ema50],
                              ['EMA 200', stock.ema200],
                              ['SMA 50', stock.sma50],
                              ['SMA 200', stock.sma200],
                            ].map(([label, val]) => (
                              <div className="ma-chip" key={label as string}>
                                <span className="ma-chip-label">{label}</span>
                                <span className="ma-chip-value">${(val as number).toFixed(2)}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        <div className="expand-section">
                          <div className="expand-label">Bollinger Bands (20, 2) &amp; ATR</div>
                          <div className="ma-grid">
                            {[
                              ['BB Lower', stock.bb_lower],
                              ['BB Mid', stock.bb_middle],
                              ['BB Upper', stock.bb_upper],
                              ['ATR 14', stock.atr14],
                            ].map(([label, val]) => (
                              <div className="ma-chip" key={label as string}>
                                <span className="ma-chip-label">{label}</span>
                                <span className="ma-chip-value">${(val as number).toFixed(2)}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        <div className="expand-section">
                          <div className="expand-label">Swing Trade Levels</div>
                          <table className="levels-table">
                            <thead>
                              <tr>
                                <th>#</th>
                                <th>Setup</th>
                                <th>Entry</th>
                                <th>Stop</th>
                                <th>Risk / share</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr>
                                <td>1</td>
                                <td>Breakout (now)</td>
                                <td className="positive">${stock.entry1.toFixed(2)}</td>
                                <td className="negative">${stock.stop1.toFixed(2)}</td>
                                <td className="risk-cell">${risk1}</td>
                              </tr>
                              <tr>
                                <td>2</td>
                                <td>EMA 8 pullback</td>
                                <td className="positive">${stock.entry2.toFixed(2)}</td>
                                <td className="negative">${stock.stop2.toFixed(2)}</td>
                                <td className="risk-cell">${risk2}</td>
                              </tr>
                              <tr>
                                <td>3</td>
                                <td>BB midline dip</td>
                                <td className="positive">${stock.entry3.toFixed(2)}</td>
                                <td className="negative">${stock.stop3.toFixed(2)}</td>
                                <td className="risk-cell">${risk3}</td>
                              </tr>
                            </tbody>
                          </table>
                        </div>

                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default React.memo(StockTable);
