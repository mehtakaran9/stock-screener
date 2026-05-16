import React from 'react';
import { TrendingUp, TrendingDown, BarChart3, Database } from 'lucide-react';

interface Stock {
  ticker: string;
  price: number;
  change: number;
  volume: number;
  market_cap: number;
  ema8: number;
  sma200: number;
}

interface StockTableProps {
  stocks: Stock[];
  onSelect?: (ticker: string) => void;
  selectedTicker?: string;
}

const StockTable: React.FC<StockTableProps> = ({ stocks, onSelect, selectedTicker }) => {
  const formatNumber = (num: number) => {
    if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
    if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    return num.toLocaleString();
  };

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
          </tr>
        </thead>
        <tbody>
          {stocks.length === 0 ? (
            <tr>
              <td colSpan={5} style={{ textAlign: 'center', padding: '2rem' }}>
                No stocks found matching criteria. Start a scan to find opportunities.
              </td>
            </tr>
          ) : (
            stocks.map((stock) => (
              <tr
                key={stock.ticker}
                onClick={() => onSelect?.(stock.ticker)}
                className={selectedTicker === stock.ticker ? 'selected' : ''}
              >
                <td className="ticker-cell">{stock.ticker}</td>
                <td>${stock.price.toFixed(2)}</td>
                <td className={stock.change >= 0 ? 'positive' : 'negative'}>
                  <div className="flex-center">
                    {stock.change >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                    {stock.change.toFixed(2)}%
                  </div>
                </td>
                <td>
                  <div className="flex-center">
                    <BarChart3 size={16} style={{ marginRight: '4px' }} />
                    {formatNumber(stock.volume)}
                  </div>
                </td>
                <td>
                  <div className="flex-center">
                    <Database size={16} style={{ marginRight: '4px' }} />
                    {formatNumber(stock.market_cap)}
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
};

export default StockTable;
