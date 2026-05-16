import { useState, useEffect, useRef } from 'react';
import { TrendingUp, Play, Loader2, RotateCcw } from 'lucide-react';
import StockTable from './components/StockTable';
import type { Stock } from './types';
import './App.css';

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface ScanProgress {
  total: number;
  current: number;
  ticker?: string;
}

function App() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [progress, setProgress] = useState<ScanProgress | null>(null);
  const [scanComplete, setScanComplete] = useState<{ total: number } | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [activeFilters, setActiveFilters] = useState<string[]>([]);
  const [scanWarning, setScanWarning] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    fetchFilters();
    startScan();
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  const fetchFilters = async () => {
    try {
      const response = await fetch(`${API_URL}/api/filters`);
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const data = await response.json();
      setActiveFilters(data.filters);
    } catch (error) {
      console.error('Error fetching filters:', error);
    }
  };

  const startScan = async () => {
    if (isScanning) return;

    setStocks([]);
    setIsScanning(true);
    setProgress(null);
    setScanComplete(null);
    setScanWarning(null);
    setStartTime(Date.now());

    try {
      await fetch(`${API_URL}/`);
    } catch {
      setIsScanning(false);
      return;
    }

    const es = new EventSource(`${API_URL}/api/scan`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.status === 'warning') {
          setScanWarning(data.message);
        } else if (data.status === 'progress') {
          setProgress({ total: data.total, current: data.current, ticker: data.ticker });
        } else if (data.status === 'result') {
          setStocks((prev) => {
            if (prev.find(s => s.ticker === data.data.ticker)) return prev;
            return [...prev, data.data];
          });
        } else if (data.status === 'complete') {
          setIsScanning(false);
          setProgress(null);
          setScanComplete({ total: data.total });
          es.close();
          eventSourceRef.current = null;
        }
      } catch (e) {
        console.error('Failed to parse SSE message:', e);
      }
    };

    es.onerror = (err) => {
      console.error('SSE Error:', err);
      setIsScanning(false);
      es.close();
      eventSourceRef.current = null;
    };
  };

  const calculateETA = () => {
    if (!progress || !startTime || progress.current === 0) return 'Calculating...';

    const elapsed = (Date.now() - startTime) / 1000;
    const rate = progress.current / elapsed;
    const remaining = (progress.total - progress.current) / rate;

    const mins = Math.floor(remaining / 60);
    const secs = Math.floor(remaining % 60);

    return `${mins}m ${secs}s`;
  };

  return (
    <div className="app-container">
      <header>
        <div className="logo">
          <TrendingUp size={24} color="#bb86fc" />
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
        <div className="left-panel">
          <div className="stats-header">
            <h2>Market Scan Results</h2>
            {progress && (
              <div className="progress-bar-container">
                <div className="progress-bar-wrapper">
                  <div 
                    className="progress-bar" 
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                </div>
                <div className="progress-meta">
                  <span className="progress-text">
                    {progress.current} / {progress.total} {progress.ticker && `(Scanning: ${progress.ticker})`}
                  </span>
                  <span className="progress-eta">ETA: {calculateETA()}</span>
                </div>
              </div>
            )}
            {scanComplete && !isScanning && (
              <div className="scan-complete-msg">
                Scan complete · {scanComplete.total} tickers scanned
              </div>
            )}
            {stocks.length > 0 && !isScanning && (
              <span className="count-badge">{stocks.length} matches found</span>
            )}
          </div>
          
          <StockTable stocks={stocks} />
        </div>
      </main>

      {scanWarning && (
        <div className="scan-warning">{scanWarning}</div>
      )}

      <div className="filters-summary">
        <span className="filters-label">Active Filters:</span>
        {activeFilters.map((filter, index) => (
          <span key={index} className="filter-tag">{filter}</span>
        ))}
      </div>
    </div>
  );
}

export default App;
