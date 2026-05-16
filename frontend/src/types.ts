export interface Stock {
  ticker: string;
  exchange: string;
  price: number;
  change: number;
  volume: number;
  vol_ratio: number;
  market_cap: number;
  rsi: number;
  macd: number;
  macd_signal: number;
  macd_hist: number;
  ema8: number;
  ema50: number;
  ema200: number;
  sma50: number;
  sma200: number;
  bb_upper: number;
  bb_middle: number;
  bb_lower: number;
  atr14: number;
  entry1: number;
  entry2: number;
  entry3: number;
  stop1: number;
  stop2: number;
  stop3: number;
}
