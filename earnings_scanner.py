"""
Scans for S&P 500 stocks with a positive EPS surprise in the last N days.
Uses Alpaca news to identify candidates quickly, then yfinance to verify the beat.
"""

import time
import requests
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from config import ALPACA_KEY, ALPACA_SECRET, MIN_PRICE, MIN_VOLUME, MIN_SURPRISE_PCT

_SP500_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "JPM", "LLY", "V",
    "UNH", "XOM", "MA", "AVGO", "HD", "PG", "COST", "JNJ", "WMT", "MRK",
    "ABBV", "CVX", "BAC", "NFLX", "KO", "PEP", "AMD", "ORCL", "CRM", "TMO",
    "ACN", "CSCO", "LIN", "MCD", "ABT", "TXN", "GE", "PM", "ISRG", "NEE",
    "DHR", "RTX", "AMGN", "INTU", "SPGI", "PFE", "HON", "CAT", "GS", "IBM",
    "QCOM", "LOW", "AMAT", "BKNG", "SBUX", "AXP", "T", "BLK", "MDT", "DE",
    "SYK", "GILD", "ADI", "VRTX", "PLD", "CB", "MMC", "TJX", "MO", "C",
    "SO", "DUK", "CL", "ZTS", "BSX", "AON", "ICE", "CME", "WM", "HCA",
    "PNC", "USB", "CI", "ELV", "SHW", "APD", "NSC", "ITW", "MCO", "ETN",
    "EMR", "GD", "NOC", "LMT", "MMM", "FDX", "UPS", "CSX", "REGN", "BIIB",
]


def get_sp500_tickers() -> list[str]:
    try:
        table = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=10
        )[0]
        tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()
        print(f"[scanner] S&P 500: {len(tickers)} tickers from Wikipedia")
        return tickers
    except Exception:
        print(f"[scanner] Wikipedia failed — using fallback ({len(_SP500_FALLBACK)} tickers)")
        return _SP500_FALLBACK


def _get_news_candidates(days_back: int) -> list[str]:
    """Return tickers mentioned in recent earnings-related Alpaca news."""
    start = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keywords = {"earnings", "eps", "quarterly", "beat", "revenue"}
    try:
        resp = requests.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers={
                "APCA-API-KEY-ID": ALPACA_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET,
            },
            params={"start": start, "limit": 50, "include_content": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        tickers = set()
        for article in resp.json().get("news", []):
            if any(kw in article.get("headline", "").lower() for kw in keywords):
                tickers.update(article.get("symbols", []))
        print(f"[scanner] {len(tickers)} tickers from earnings news")
        return list(tickers)
    except Exception as e:
        print(f"[scanner] News API error: {e}")
        return []


def _check_beat(sym: str, days_back: int) -> dict | None:
    """Return beat dict if this ticker had a positive EPS surprise recently, else None."""
    cutoff = datetime.now() - timedelta(days=days_back + 1)
    try:
        tk = yf.Ticker(sym)
        ed = tk.earnings_dates
        if ed is None or ed.empty:
            return None

        for date_idx, row in ed.iterrows():
            # Normalize to naive datetime
            try:
                naive = date_idx.tz_convert(None).to_pydatetime()
            except Exception:
                naive = pd.Timestamp(date_idx).to_pydatetime().replace(tzinfo=None)

            if naive < cutoff:
                break  # earnings_dates is newest-first

            estimate = row.get("EPS Estimate")
            actual   = row.get("Reported EPS")
            if pd.isna(estimate) or pd.isna(actual) or estimate == 0:
                continue

            surprise_pct = (float(actual) - float(estimate)) / abs(float(estimate)) * 100
            if surprise_pct < MIN_SURPRISE_PCT * 100:
                return None

            # Price + volume filter
            info   = tk.fast_info
            price  = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None) or 0
            volume = getattr(info, "three_month_average_volume", None) or 0
            sector = (tk.info or {}).get("sector", "unknown")

            if float(price) < MIN_PRICE or float(volume) < MIN_VOLUME:
                return None

            return {
                "ticker":        sym,
                "surprise_pct":  round(surprise_pct, 2),
                "actual_eps":    round(float(actual), 4),
                "estimated_eps": round(float(estimate), 4),
                "price":         round(float(price), 2),
                "sector":        sector,
                "report_date":   str(naive.date()),
            }
    except Exception:
        return None
    return None


def get_earnings_beats(days_back: int = 2) -> list[dict]:
    """Return stocks that beat EPS in last days_back days, sorted by surprise % desc."""
    # Start with fast news-based candidates; fall back to full S&P 500
    candidates = _get_news_candidates(days_back)
    if len(candidates) < 5:
        candidates = list(set(candidates) | set(get_sp500_tickers()))

    print(f"[scanner] Checking {len(candidates)} tickers for earnings beats…")
    beats = []
    for i, sym in enumerate(candidates):
        if i > 0 and i % 100 == 0:
            print(f"[scanner]  … {i}/{len(candidates)} checked, {len(beats)} beats so far")
            time.sleep(1)
        result = _check_beat(sym, days_back)
        if result:
            beats.append(result)
            print(f"[scanner]  ✓ {sym:6} +{result['surprise_pct']:.1f}% EPS surprise")

    beats.sort(key=lambda x: x["surprise_pct"], reverse=True)
    print(f"[scanner] {len(beats)} earnings beats found (≥{MIN_SURPRISE_PCT:.0%} surprise)")
    return beats
