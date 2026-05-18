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
  const [activeFilters, setActiveFilters] = useState<string[]>([]);
  const [scanWarning, setScanWarning] = useState<string | null>(null);
  const [isWaking, setIsWaking] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const wakeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wakeControllerRef = useRef<AbortController | null>(null);
  const smoothedRateRef = useRef<number>(0);
  const prevProgressRef = useRef<{ time: number; current: number } | null>(null);

  useEffect(() => {
    fetchFilters();
    return () => {
      eventSourceRef.current?.close();
      if (wakeTimerRef.current) clearTimeout(wakeTimerRef.current);
      wakeControllerRef.current?.abort();
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
    smoothedRateRef.current = 0;
    prevProgressRef.current = null;

    setIsWaking(true);
    const controller = new AbortController();
    wakeControllerRef.current = controller;
    const wakeTimer = setTimeout(() => controller.abort(), 75_000);
    wakeTimerRef.current = wakeTimer;
    try {
      await fetch(`${API_URL}/`, { signal: controller.signal });
    } catch {
      clearTimeout(wakeTimer);
      wakeTimerRef.current = null;
      setIsWaking(false);
      setIsScanning(false);
      setScanWarning('Backend is waking up — please try again in a moment.');
      return;
    }
    clearTimeout(wakeTimer);
    wakeTimerRef.current = null;
    setIsWaking(false);

    const es = new EventSource(`${API_URL}/api/scan`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.status === 'warning') {
          setScanWarning(data.message);
        } else if (data.status === 'progress') {
          if (data.current > 0) {
            const now = Date.now();
            const prev = prevProgressRef.current;
            if (prev && now > prev.time) {
              const instantRate = (data.current - prev.current) / ((now - prev.time) / 1000);
              if (instantRate > 0) {
                smoothedRateRef.current = smoothedRateRef.current === 0
                  ? instantRate
                  : 0.1 * instantRate + 0.9 * smoothedRateRef.current;
              }
            }
            prevProgressRef.current = { time: now, current: data.current };
          }
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
      setScanWarning('Scan connection lost — please try again.');
      setIsScanning(false);
      es.close();
      eventSourceRef.current = null;
    };
  };

  const calculateETA = () => {
    if (!progress || progress.current === 0) return 'Downloading data...';
    const rate = smoothedRateRef.current;
    if (rate === 0) return 'Calculating...';
    const remaining = (progress.total - progress.current) / rate;
    const rounded = Math.ceil(remaining / 5) * 5;
    const mins = Math.floor(rounded / 60);
    const secs = rounded % 60;
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
            aria-busy={isScanning}
          >
            {isScanning ? <Loader2 className="animate-spin" /> : <Play size={18} />}
            {isScanning ? (isWaking ? 'Waking up...' : 'Scanning...') : 'Start Scan'}
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
        {activeFilters.map((filter) => (
          <span key={filter} className="filter-tag">{filter}</span>
        ))}
      </div>
    </div>
  );
}

export default App;
