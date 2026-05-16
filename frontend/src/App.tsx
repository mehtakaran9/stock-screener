import React, { useState, useEffect } from 'react';
import { Search, Play, Loader2, RotateCcw } from 'lucide-react';
import StockTable from './components/StockTable';
import StockChart from './components/StockChart';
import './App.css';

interface Stock {
  ticker: string;
  price: number;
  change: number;
  volume: number;
  market_cap: number;
  ema8: number;
  sma200: number;
}

interface ScanProgress {
  total: number;
  current: number;
}

function App() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [progress, setProgress] = useState<ScanProgress | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [chartData, setChartData] = useState<any[] | null>(null);
  const [isLoadingChart, setIsLoadingChart] = useState(false);

  const startScan = () => {
    setStocks([]);
    setIsScanning(true);
    setProgress(null);
    setSelectedTicker(null);
    setChartData(null);

    const eventSource = new EventSource('http://localhost:8000/api/scan');

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.status === 'progress') {
        setProgress({ total: data.total, current: data.current });
      } else if (data.status === 'result') {
        setStocks((prev) => [...prev, data.data]);
      } else if (data.status === 'complete') {
        setIsScanning(false);
        eventSource.close();
      }
    };

    eventSource.onerror = (err) => {
      console.error('SSE Error:', err);
      setIsScanning(false);
      eventSource.close();
    };
  };

  const handleSelectStock = async (ticker: string) => {
    setSelectedTicker(ticker);
    setIsLoadingChart(true);
    try {
      const response = await fetch(`http://localhost:8000/api/history/${ticker}`);
      const data = await response.json();
      setChartData(data);
    } catch (error) {
      console.error('Error fetching history:', error);
    } finally {
      setIsLoadingChart(false);
    }
  };

  return (
    <div className="app-container">
      <header>
        <div className="logo">
          <Search size={24} color="#3b82f6" />
          <h1>StockScreener Pro</h1>
        </div>
        <div className="controls">
          <button 
            className={`btn-primary ${isScanning ? 'loading' : ''}`} 
            onClick={startScan} 
            disabled={isScanning}
          >
            {isScanning ? <Loader2 className="animate-spin" /> : <Play size={18} />}
            {isScanning ? 'Scanning...' : 'Start Scan'}
          </button>
          <button className="btn-secondary" onClick={() => window.location.reload()}>
            <RotateCcw size={18} />
            Reset
          </button>
        </div>
      </header>

      <main>
        <div className="dashboard-grid">
          <div className="left-panel">
            <div className="stats-header">
              <h2>Market Scan Results</h2>
              {progress && (
                <div className="progress-bar-container">
                  <div 
                    className="progress-bar" 
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                  <span>Scanning {progress.total} Tickers...</span>
                </div>
              )}
              {stocks.length > 0 && !isScanning && (
                <span className="count-badge">{stocks.length} matches found</span>
              )}
            </div>
            
            <StockTable 
              stocks={stocks} 
              onSelect={handleSelectStock} 
              selectedTicker={selectedTicker || undefined} 
            />
          </div>

          <div className="right-panel">
            {selectedTicker ? (
              <div className="detail-view">
                {isLoadingChart ? (
                  <div className="loader-container">
                    <Loader2 className="animate-spin" size={48} />
                    <p>Loading Chart Data...</p>
                  </div>
                ) : chartData ? (
                  <StockChart data={chartData} ticker={selectedTicker} />
                ) : (
                  <div className="empty-state">
                    <p>Failed to load chart data.</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-state">
                <BarChart3 size={64} opacity={0.2} />
                <h3>No Stock Selected</h3>
                <p>Click on a row in the table to view its technical chart and details.</p>
              </div>
            )}
          </div>
        </div>
      </main>

      <div className="filters-summary">
        <strong>Active Filters:</strong>
        <span className="filter-tag">Day Change &gt; 3%</span>
        <span className="filter-tag">Market Cap &gt; $1B</span>
        <span className="filter-tag">Price &gt; $5</span>
        <span className="filter-tag">Breakout 1Y Resistance</span>
        <span className="filter-tag">Riding 8EMA</span>
        <span className="filter-tag">Above 200SMA</span>
        <span className="filter-tag">Volume &gt; 500K</span>
      </div>
    </div>
  );
}

export default App;
