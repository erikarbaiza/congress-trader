"""
Scans for S&P 500 stocks with a positive EPS surprise in the last N days.
Checks the full S&P 500 on every run (Wikipedia list, saved snapshot as fallback) with yfinance.

yfinance returns empty data without raising when it is rate-limited, so an empty result is retried and, if it
persists, reported as a failed download instead of being treated as "no earnings".
"""

import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from config import MIN_PRICE, MIN_VOLUME, MIN_SURPRISE_PCT, MIN_EPS_ESTIMATE
from fetch_utils import run_in_batches

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SNAPSHOT = Path(__file__).parent / "ls_sp500.json"   # last good list, shared with the long/short module
_FAILED = object()                                    # sentinel: data could not be downloaded


def get_sp500_tickers() -> list[str]:
    """Full S&P 500 from Wikipedia; if that fails, the last saved snapshot. Never a partial list."""
    try:
        html = requests.get(_WIKI_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        tickers = pd.read_html(io.StringIO(html))[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        if len(tickers) > 400:
            _SNAPSHOT.write_text(json.dumps(sorted(tickers)), encoding="utf-8")
            print(f"[scanner] S&P 500: {len(tickers)} tickers from Wikipedia")
            return tickers
    except Exception as e:
        print(f"[scanner] Wikipedia failed ({e})")
    tickers = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    print(f"[scanner] using saved snapshot ({len(tickers)} tickers)")
    return tickers


def _market_data(tk: yf.Ticker, attempts: int = 3) -> tuple[float, float, float | None]:
    """(price, avg volume, market cap); price 0 means the download failed."""
    for i in range(attempts):
        try:
            info   = tk.fast_info
            price  = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None) or 0
            volume = getattr(info, "three_month_average_volume", None) or 0
            if price and volume:
                return float(price), float(volume), getattr(info, "market_cap", None)
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return 0.0, 0.0, None


def _sector(tk: yf.Ticker) -> str:
    for _ in range(2):
        try:
            sector = (tk.info or {}).get("sector")
            if sector:
                return sector
        except Exception:
            pass
        time.sleep(1.5)
    return "unknown"


def _check_beat(sym: str, days_back: int, attempts: int = 3, pause: float = 1.5):
    """Beat dict if this ticker had a positive EPS surprise recently, None if not, _FAILED if data could not be read."""
    cutoff = datetime.now() - timedelta(days=days_back + 1)
    tk = yf.Ticker(sym)
    ed = None
    for i in range(attempts):
        try:
            ed = tk.earnings_dates
        except Exception:
            ed = None
        if ed is not None and not ed.empty:
            break
        time.sleep(pause * (i + 1))
    if ed is None or ed.empty:
        return _FAILED

    try:
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
            if pd.isna(estimate) or pd.isna(actual) or abs(float(estimate)) < MIN_EPS_ESTIMATE:
                continue

            surprise_pct = (float(actual) - float(estimate)) / abs(float(estimate)) * 100
            if surprise_pct < MIN_SURPRISE_PCT * 100:
                return None

            # Price + volume filter
            price, volume, market_cap = _market_data(tk)
            if not price:
                return _FAILED
            if price < MIN_PRICE or volume < MIN_VOLUME:
                return None

            return {
                "ticker":        sym,
                "surprise_pct":  round(surprise_pct, 2),
                "actual_eps":    round(float(actual), 4),
                "estimated_eps": round(float(estimate), 4),
                "price":         round(price, 2),
                "sector":        _sector(tk),
                "market_cap":    market_cap,
                "report_date":   str(naive.date()),
            }
    except Exception as e:
        print(f"[scanner] {sym}: error while evaluating — {e}")
        return _FAILED
    return None


def retry_failed(failed: list[str], days_back: int, check=None, sleep=time.sleep, now=time.monotonic,
                 rounds: int = 3, cooldown: float = 60.0, budget: float = 420.0) -> tuple[list[dict], list[str]]:
    """Retry tickers whose download failed. yfinance throttles by request volume, and a throttle that has started
    does not clear in a few seconds, so each round first lets it cool down (60 s, 120 s, 180 s), then goes one
    ticker at a time with a pause between requests. `budget` caps the total time so a persistent block cannot
    stall the run. Returns (beats found, tickers still failing)."""
    check = check or _check_beat
    beats: list[dict] = []
    start = now()
    for rnd in range(1, rounds + 1):
        if not failed or now() - start >= budget:
            break
        print(f"[scanner] retry round {rnd}/{rounds}: {len(failed)} tickers, cooling down {cooldown * rnd:.0f}s first")
        sleep(cooldown * rnd)
        still = []
        for i, sym in enumerate(failed):
            if now() - start >= budget:
                still.extend(failed[i:])
                print(f"[scanner] retry budget of {budget:.0f}s used up")
                break
            r = check(sym, days_back, attempts=2, pause=2.0)
            sleep(0.7)                                   # spread requests out instead of bursting
            if isinstance(r, dict):
                beats.append(r)
            elif r is _FAILED:
                still.append(sym)
        failed = still
    return beats, failed


def get_earnings_beats(days_back: int = 2) -> list[dict]:
    """Return stocks that beat EPS in last days_back days, sorted by surprise % desc."""
    tickers = get_sp500_tickers()
    print(f"[scanner] Checking {len(tickers)} tickers for earnings beats…")

    pairs  = run_in_batches(tickers, lambda s: _check_beat(s, days_back))
    beats  = [r for _, r in pairs if isinstance(r, dict)]
    failed = [s for s, r in pairs if r is _FAILED]

    if failed:
        print(f"[scanner] {len(failed)} tickers failed on the first pass; retrying after a cool-down")
        more, failed = retry_failed(failed, days_back)
        beats += more
    if failed:
        print(f"[scanner] WARNING: no earnings data after retries for {len(failed)} tickers: {failed}")
        print(f"::warning::scanner could not read earnings for {len(failed)} tickers")

    beats.sort(key=lambda x: x["surprise_pct"], reverse=True)
    for b in beats:
        print(f"[scanner]  ✓ {b['ticker']:6} +{b['surprise_pct']:.1f}% EPS surprise")
    print(f"[scanner] {len(beats)} earnings beats found (≥{MIN_SURPRISE_PCT:.0%} surprise)")
    return beats
