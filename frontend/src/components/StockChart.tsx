import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries, LineSeries, type CandlestickData, type LineData, type Time } from 'lightweight-charts';

interface StockChartProps {
  data: any[];
  ticker: string;
}

const StockChart: React.FC<StockChartProps> = ({ data, ticker }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#1a1a1a' },
        textColor: '#d1d4dc',
      },
      grid: {
        vertLines: { color: '#334155' },
        horzLines: { color: '#334155' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    const ema8Series = chart.addSeries(LineSeries, {
      color: '#3b82f6',
      lineWidth: 2,
      title: '8 EMA',
    });

    const sma200Series = chart.addSeries(LineSeries, {
      color: '#eab308',
      lineWidth: 2,
      title: '200 SMA',
    });

    const chartData: CandlestickData<Time>[] = data.map(d => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const emaData: LineData<Time>[] = data
      .filter(d => d.ema8 !== null)
      .map(d => ({
        time: d.time as Time,
        value: d.ema8,
      }));

    const smaData: LineData<Time>[] = data
      .filter(d => d.sma200 !== null)
      .map(d => ({
        time: d.time as Time,
        value: d.sma200,
      }));

    candlestickSeries.setData(chartData);
    ema8Series.setData(emaData);
    sma200Series.setData(smaData);

    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: chartContainerRef.current?.clientWidth });
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [data]);

  return (
    <div className="stock-chart-container">
      <h3>{ticker} Analysis</h3>
      <div ref={chartContainerRef} style={{ width: '100%', height: '400px' }} />
    </div>
  );
};

export default StockChart;
