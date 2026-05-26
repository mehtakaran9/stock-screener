"""
Alternative data fetchers for the 30%+ move prediction system.

Four independent, cacheable data sources:
  SecEdgarFetcher   — insider buying from SEC Form 4 (free, 10 req/sec)
  FinraShortFetcher — daily short-sale volume from FINRA (free, ~2yr history)
  EarningsFetcher   — earnings surprise history via yfinance (3–5yr)
  PolygonFetcher    — options flow, insider, short interest (requires POLYGON_API_KEY)

Each class exposes a .get(ticker) method that returns a tidy DataFrame and caches
results to backend/_backtest_cache/ so subsequent runs are instant.
"""
import os
import time
import pathlib
import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from io import StringIO

logger = logging.getLogger(__name__)

CACHE_DIR = pathlib.Path(__file__).parent / "_backtest_cache"
CACHE_DIR.mkdir(exist_ok=True)

# ─── SEC EDGAR — Form 4 insider buys ─────────────────────────────────────────

class SecEdgarFetcher:
    """
    Fetches insider purchase transactions from SEC EDGAR Form 4 filings.
    Uses the official EDGAR data API (no key required, 10 req/sec limit).

    .get(ticker) → DataFrame[date, insider_name, role, shares, price, total_value]
    Only includes transaction codes P (open-market purchase).
    """

    _CIK_CACHE = CACHE_DIR / "sec_cik_map.pkl"
    _HEADERS   = {"User-Agent": "StockScreener mehtakaran9@gmail.com"}

    def __init__(self):
        self._cik_map: dict[str, str] = {}
        self._load_cik_map()

    def _load_cik_map(self):
        if self._CIK_CACHE.exists():
            self._cik_map = pd.read_pickle(self._CIK_CACHE)
            return
        logger.info("Downloading SEC CIK map …")
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=self._HEADERS, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._cik_map = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in data.values()
        }
        pd.to_pickle(self._cik_map, self._CIK_CACHE)

    def _cache_path(self, ticker: str) -> pathlib.Path:
        return CACHE_DIR / f"sec_form4_{ticker.upper()}.pkl"

    def get(self, ticker: str, refresh: bool = False) -> pd.DataFrame:
        """Return insider purchase DataFrame for ticker, cached on disk."""
        cache = self._cache_path(ticker)
        if cache.exists() and not refresh:
            return pd.read_pickle(cache)

        cik = self._cik_map.get(ticker.upper())
        if not cik:
            logger.debug(f"No CIK found for {ticker}")
            return pd.DataFrame()

        df = self._fetch_form4(ticker, cik)
        df.to_pickle(cache)
        return df

    def _fetch_form4(self, ticker: str, cik: str) -> pd.DataFrame:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            time.sleep(0.12)  # respect 10 req/sec
            resp = requests.get(url, headers=self._HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"SEC EDGAR fetch failed for {ticker}: {e}")
            return pd.DataFrame()

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms      = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filed_dates = recent.get("filingDate", [])

        rows = []
        for form, accn, filed in zip(forms, accessions, filed_dates):
            if form != "4":
                continue
            acc_clean = accession = accn.replace("-", "")
            xml_url = (
                f"https://www.sec.gov/Archives/edgar/full-index/"
                f"{filed[:4]}/{filed[5:7]}/{filed[8:10]}/{acc_clean}.txt"
            )
            # Simpler: pull the primary XML document from the filing index
            index_url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=40&search_text="
            )
            # Use the structured XBRL endpoint instead
            filing_url = (
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            )
            # Parse the non-derivative transaction table from the filing
            parsed = self._parse_form4_filing(cik, accn, filed)
            rows.extend(parsed)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        return df

    def _parse_form4_filing(self, cik: str, accn: str, filed: str) -> list[dict]:
        """Download and parse a single Form 4 XML filing."""
        acc_fmt = accn.replace("-", "")
        # Build URL to the filing index to find the XML document
        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_fmt}/{accn}-index.htm"
        )
        try:
            time.sleep(0.12)
            resp = requests.get(index_url, headers=self._HEADERS, timeout=20)
            if resp.status_code != 200:
                return []
            # Find the .xml form4 document link
            import re
            xml_match = re.search(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
            if not xml_match:
                return []
            xml_url = "https://www.sec.gov" + xml_match.group(1)
            time.sleep(0.12)
            xml_resp = requests.get(xml_url, headers=self._HEADERS, timeout=20)
            if xml_resp.status_code != 200:
                return []
            return self._extract_purchases(xml_resp.text, filed)
        except Exception:
            return []

    def _extract_purchases(self, xml_text: str, filed: str) -> list[dict]:
        """Extract open-market purchase transactions from Form 4 XML."""
        import xml.etree.ElementTree as ET
        rows = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        # Reporting owner name
        owner_name = ""
        for tag in ["rptOwnerName", "reportingOwnerId/rptOwnerName"]:
            el = root.find(f".//{tag}")
            if el is not None and el.text:
                owner_name = el.text.strip()
                break

        # Officer/director role
        role = ""
        for tag in ["officerTitle", "isOfficer", "isDirector"]:
            el = root.find(f".//{tag}")
            if el is not None and el.text:
                role = el.text.strip()
                break

        # Non-derivative transactions
        for trans in root.findall(".//nonDerivativeTransaction"):
            code_el = trans.find(".//transactionCode")
            if code_el is None or code_el.text != "P":
                continue
            date_el   = trans.find(".//transactionDate/value")
            shares_el = trans.find(".//transactionShares/value")
            price_el  = trans.find(".//transactionPricePerShare/value")
            if date_el is None or shares_el is None:
                continue
            try:
                shares = float(shares_el.text)
                price  = float(price_el.text) if price_el is not None else 0.0
                rows.append({
                    "date":        date_el.text.strip(),
                    "insider_name": owner_name,
                    "role":        role,
                    "shares":      shares,
                    "price":       price,
                    "total_value": round(shares * price, 2),
                })
            except (ValueError, TypeError):
                continue
        return rows

    def get_bulk_summary(
        self, tickers: list[str], lookback_days: int = 90
    ) -> pd.DataFrame:
        """
        For a list of tickers return a summary DataFrame:
          ticker | insider_buy_count | insider_buy_value | last_buy_date
        Covers the past `lookback_days` days from today.
        """
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
        rows = []
        for i, t in enumerate(tickers, 1):
            df = self.get(t)
            if df.empty:
                rows.append({"ticker": t, "insider_buy_count": 0,
                             "insider_buy_value": 0.0, "last_buy_date": pd.NaT})
                continue
            recent = df[df["date"] >= cutoff]
            rows.append({
                "ticker":            t,
                "insider_buy_count": len(recent),
                "insider_buy_value": recent["total_value"].sum(),
                "last_buy_date":     recent["date"].max() if len(recent) else pd.NaT,
            })
            if i % 50 == 0:
                print(f"  SEC Edgar: {i}/{len(tickers)} tickers …", end="\r", flush=True)
        print()
        return pd.DataFrame(rows)


# ─── FINRA — daily short-sale volume ─────────────────────────────────────────

class FinraShortFetcher:
    """
    Downloads daily short-sale volume data from FINRA.
    short_vol_ratio = short_volume / total_volume (proxy for short interest intensity).

    .get_for_date(date_str) → DataFrame[symbol, short_vol_ratio] for that trading day
    .get_history(ticker, start, end) → DataFrame[date, short_vol_ratio] for one ticker
    """

    # FINRA consolidated short volume (both exchanges)
    _URL_TEMPLATE = (
        "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
    )
    # Format: YYYYMMDD

    def _cache_path(self, date_str: str) -> pathlib.Path:
        return CACHE_DIR / f"finra_short_{date_str}.pkl"

    def get_for_date(self, date_str: str, refresh: bool = False) -> pd.DataFrame:
        """
        Fetch FINRA short volume for a single trading date.
        date_str: 'YYYY-MM-DD' or 'YYYYMMDD'
        """
        date_clean = date_str.replace("-", "")
        cache = self._cache_path(date_clean)
        if cache.exists() and not refresh:
            return pd.read_pickle(cache)

        url = self._URL_TEMPLATE.format(date=date_clean)
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 404:
                return pd.DataFrame()
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), sep="|", on_bad_lines="skip")
            # Column names vary; normalise
            df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
            # Expected: symbol | date | shortvolume | shortexemptvolume | totalvolume | market
            if "symbol" not in df.columns or "shortvolume" not in df.columns:
                return pd.DataFrame()
            df = df[df["symbol"].str.len() <= 5].copy()
            df["short_vol_ratio"] = pd.to_numeric(df["shortvolume"], errors="coerce") / \
                                    pd.to_numeric(df["totalvolume"], errors="coerce").replace(0, pd.NA)
            out = df[["symbol", "short_vol_ratio"]].rename(columns={"symbol": "ticker"})
            out = out.dropna(subset=["short_vol_ratio"])
            out.to_pickle(cache)
            return out
        except Exception as e:
            logger.warning(f"FINRA fetch failed for {date_clean}: {e}")
            return pd.DataFrame()

    def get_history(
        self, ticker: str, start: str, end: str
    ) -> pd.DataFrame:
        """
        Build a per-day short_vol_ratio series for one ticker between start and end.
        Iterates over trading days and fetches each FINRA file (cached per day).
        Returns DataFrame[date, short_vol_ratio].
        """
        dates = pd.bdate_range(start=start, end=end)
        rows = []
        for d in dates:
            date_str = d.strftime("%Y%m%d")
            day_df = self.get_for_date(date_str)
            if day_df.empty:
                continue
            match = day_df[day_df["ticker"] == ticker.upper()]
            if not match.empty:
                rows.append({"date": d, "short_vol_ratio": match["short_vol_ratio"].iloc[0]})
        return pd.DataFrame(rows)

    def get_snapshot(self, ticker: str) -> float | None:
        """Current short percent of float from yfinance (snapshot, not historical)."""
        try:
            info = yf.Ticker(ticker).info
            pct = info.get("shortPercentOfFloat")
            return float(pct) if pct is not None else None
        except Exception:
            return None


# ─── Earnings surprise history ───────────────────────────────────────────────

class EarningsFetcher:
    """
    Fetches earnings surprise history per ticker.
    Primary source: yfinance earnings_history (3–5yr).
    Fallback: Alpha Vantage free tier (requires ALPHA_VANTAGE_KEY env var, 25 req/day).

    .get(ticker) → DataFrame[date, eps_estimate, eps_actual, surprise_pct]
    Also computes:
      beat_streak    — consecutive quarters of positive surprise (as of each date)
      avg_surprise_4q — rolling 4-quarter average surprise %
    """

    _AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

    def _cache_path(self, ticker: str) -> pathlib.Path:
        return CACHE_DIR / f"earnings_{ticker.upper()}.pkl"

    def get(self, ticker: str, refresh: bool = False) -> pd.DataFrame:
        cache = self._cache_path(ticker)
        if cache.exists() and not refresh:
            return pd.read_pickle(cache)

        df = self._from_yfinance(ticker)
        if df.empty and self._AV_KEY:
            df = self._from_alpha_vantage(ticker)

        df = self._enrich(df)
        df.to_pickle(cache)
        return df

    def _from_yfinance(self, ticker: str) -> pd.DataFrame:
        try:
            hist = yf.Ticker(ticker).earnings_history
            if hist is None or hist.empty:
                return pd.DataFrame()
            hist = hist.reset_index()
            # Normalise column names across yfinance versions
            rename = {}
            for col in hist.columns:
                lc = col.lower().replace(" ", "_")
                if "estimate" in lc:    rename[col] = "eps_estimate"
                elif "actual" in lc:    rename[col] = "eps_actual"
                elif "surprise" in lc and "%" in col: rename[col] = "surprise_pct"
                elif "surprise" in lc:  rename[col] = "surprise_pct"
                elif "date" in lc or col == "Earnings Date": rename[col] = "date"
            hist = hist.rename(columns=rename)
            for col in ["eps_estimate", "eps_actual", "surprise_pct"]:
                if col not in hist.columns:
                    hist[col] = pd.NA
            hist["date"] = pd.to_datetime(hist["date"], errors="coerce", utc=True)
            hist["date"] = hist["date"].dt.tz_localize(None)
            return hist[["date", "eps_estimate", "eps_actual", "surprise_pct"]].dropna(subset=["date"])
        except Exception as e:
            logger.debug(f"yfinance earnings failed for {ticker}: {e}")
            return pd.DataFrame()

    def _from_alpha_vantage(self, ticker: str) -> pd.DataFrame:
        url = "https://www.alphavantage.co/query"
        params = {"function": "EARNINGS", "symbol": ticker, "apikey": self._AV_KEY}
        try:
            time.sleep(1.2)  # free tier: 5 req/min
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json().get("quarterlyEarnings", [])
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            df["date"]         = pd.to_datetime(df["reportedDate"], errors="coerce")
            df["eps_estimate"] = pd.to_numeric(df["estimatedEPS"],  errors="coerce")
            df["eps_actual"]   = pd.to_numeric(df["reportedEPS"],   errors="coerce")
            df["surprise_pct"] = pd.to_numeric(df["surprisePercentage"], errors="coerce")
            return df[["date", "eps_estimate", "eps_actual", "surprise_pct"]].dropna(subset=["date"])
        except Exception as e:
            logger.debug(f"Alpha Vantage earnings failed for {ticker}: {e}")
            return pd.DataFrame()

    def _enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.sort_values("date").reset_index(drop=True)
        df["surprise_pct"] = pd.to_numeric(df["surprise_pct"], errors="coerce")
        is_beat = (df["surprise_pct"] > 0).astype(int)
        # rolling beat streak: consecutive beats ending at each row
        streak = []
        run = 0
        for beat in is_beat:
            run = run + 1 if beat else 0
            streak.append(run)
        df["beat_streak"]     = streak
        df["avg_surprise_4q"] = df["surprise_pct"].rolling(4, min_periods=1).mean().round(2)
        return df

    def days_since_last_earnings(self, ticker: str, as_of: pd.Timestamp) -> int | None:
        """Return calendar days since the most recent earnings report before `as_of`."""
        df = self.get(ticker)
        if df.empty:
            return None
        past = df[df["date"] < as_of]
        if past.empty:
            return None
        return (as_of - past["date"].max()).days

    def beat_streak_as_of(self, ticker: str, as_of: pd.Timestamp) -> int:
        """Return the consecutive beat streak as of a given date."""
        df = self.get(ticker)
        if df.empty:
            return 0
        past = df[df["date"] < as_of]
        if past.empty:
            return 0
        return int(past.iloc[-1]["beat_streak"])


# ─── Polygon.io (optional) ───────────────────────────────────────────────────

class PolygonFetcher:
    """
    Fetches options flow, insider transactions, and short interest via Polygon.io.
    Requires POLYGON_API_KEY environment variable.

    Instantiation will raise RuntimeError if the key is not set.

    .get_put_call_ratio(ticker, date) → float (daily put/call ratio)
    .get_insider_buys(ticker, months) → DataFrame[date, name, shares, value]
    .get_iv_percentile(ticker, date)  → float (IV rank 0–100)
    """

    def __init__(self):
        self._key = os.environ.get("POLYGON_API_KEY", "")
        if not self._key:
            raise RuntimeError(
                "POLYGON_API_KEY environment variable not set. "
                "Sign up at polygon.io ($29/mo) and set the key to enable options flow."
            )
        try:
            from polygon import RESTClient  # type: ignore
            self._client = RESTClient(api_key=self._key)
        except ImportError:
            raise RuntimeError(
                "polygon-api-client not installed. Run: pip install polygon-api-client"
            )

    def get_put_call_ratio(self, ticker: str, date: str) -> float | None:
        """Daily put/call volume ratio for ticker on given date (YYYY-MM-DD)."""
        try:
            snap = self._client.get_snapshot_option_chain(
                ticker,
                params={"as_of": date, "limit": 250},
            )
            results = list(snap)
            if not results:
                return None
            puts  = sum(r.day.volume for r in results if r.details.contract_type == "put"  and r.day)
            calls = sum(r.day.volume for r in results if r.details.contract_type == "call" and r.day)
            return round(puts / calls, 3) if calls else None
        except Exception as e:
            logger.debug(f"Polygon put/call ratio failed for {ticker} {date}: {e}")
            return None

    def get_insider_buys(self, ticker: str, months: int = 3) -> pd.DataFrame:
        """Insider purchase transactions from Polygon (Form 4 equivalent)."""
        since = (datetime.now() - timedelta(days=30 * months)).strftime("%Y-%m-%d")
        rows = []
        try:
            for txn in self._client.list_insider_transactions(
                ticker, transaction_type="buy", filing_date_gte=since
            ):
                rows.append({
                    "date":         txn.filing_date,
                    "insider_name": getattr(txn, "name", ""),
                    "shares":       getattr(txn, "shares", 0),
                    "price":        getattr(txn, "price", 0.0),
                    "total_value":  getattr(txn, "shares", 0) * getattr(txn, "price", 0.0),
                })
        except Exception as e:
            logger.debug(f"Polygon insider buys failed for {ticker}: {e}")
        return pd.DataFrame(rows)

    def get_iv_percentile(self, ticker: str, date: str) -> float | None:
        """IV rank (0–100) for ticker as of date. Requires options data."""
        try:
            snap = self._client.get_snapshot_option_chain(
                ticker, params={"as_of": date, "limit": 1}
            )
            results = list(snap)
            if results and hasattr(results[0], "implied_volatility"):
                return float(results[0].implied_volatility)
            return None
        except Exception:
            return None


# ─── Convenience: check what's available ─────────────────────────────────────

def available_sources() -> dict[str, bool]:
    """Return which data sources are configured and usable."""
    polygon_ok = bool(os.environ.get("POLYGON_API_KEY"))
    av_ok      = bool(os.environ.get("ALPHA_VANTAGE_KEY"))
    try:
        if polygon_ok:
            from polygon import RESTClient  # noqa
    except ImportError:
        polygon_ok = False
    return {
        "sec_edgar":    True,          # always free
        "finra_short":  True,          # always free
        "yfinance_earnings": True,     # always free (3–5yr depth)
        "alpha_vantage_earnings": av_ok,
        "polygon":      polygon_ok,
    }


if __name__ == "__main__":
    print("Available sources:", available_sources())
    print("\nTesting SEC EDGAR (AAPL) …")
    sec = SecEdgarFetcher()
    df = sec.get("AAPL")
    print(f"  {len(df)} Form 4 purchase transactions found")
    if not df.empty:
        print(df.tail(3).to_string(index=False))

    print("\nTesting EarningsFetcher (AAPL) …")
    ef = EarningsFetcher()
    edf = ef.get("AAPL")
    print(f"  {len(edf)} earnings periods found")
    if not edf.empty:
        print(edf.tail(4).to_string(index=False))
