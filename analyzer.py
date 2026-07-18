"""
Ranks Congress members by a composite score based on:
  - Weighted average 12-month return (40%)
  - Win rate — % of profitable trades (30%)
  - Sharpe ratio (20%)
  - Trade count (10%)

Final score is 0–100.
"""

import json
import math
from datetime import datetime, timedelta, timezone, date as date_type
from collections import defaultdict
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from config import ALPACA_KEY, ALPACA_SECRET, MIN_TRADES, MAX_POSITIONS

_SECTORS_CACHE_FILE = Path(__file__).parent / "sectors_cache.json"


def _load_sectors_cache() -> dict[str, str]:
    if _SECTORS_CACHE_FILE.exists():
        try:
            return json.loads(_SECTORS_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_sectors_cache(cache: dict[str, str]) -> None:
    try:
        _SECTORS_CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _get_sectors(tickers: list[str]) -> dict[str, str]:
    """Return GICS sector for each ticker, hitting yfinance only for uncached tickers."""
    import yfinance as yf

    cache = _load_sectors_cache()
    missing = [t for t in tickers if t not in cache]

    if missing:
        print(f"[analyzer] Fetching sectors from yfinance for {len(missing)} new tickers…")
        for sym in missing:
            try:
                sector = yf.Ticker(sym).info.get("sector") or "unknown"
            except Exception:
                sector = "unknown"
            cache[sym] = sector
        _save_sectors_cache(cache)
        print(f"[analyzer] Sector cache now has {len(cache)} tickers.")

    return {t: cache.get(t, "unknown") for t in tickers}

_data_client = None


def _get_data_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
    return _data_client


def _fetch_close_price(ticker: str, date_str: str) -> float | None:
    try:
        start = datetime.fromisoformat(date_str[:10])
        end = start + timedelta(days=7)
        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            limit=1,
            feed=DataFeed.IEX,
        )
        bars = _get_data_client().get_stock_bars(req)
        df = bars.df
        if df.empty:
            return None
        return float(df["close"].iloc[0])
    except Exception:
        return None


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close price for each ticker, batching to avoid API limits."""
    if not tickers:
        return {}

    result = {}
    batch_size = 100

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
            bars = _get_data_client().get_stock_bars(req)
            df = bars.df
            if df.empty:
                continue
            for sym in batch:
                try:
                    if "symbol" in df.index.names:
                        sym_df = df.xs(sym, level="symbol")
                    else:
                        sym_df = df[df.index.get_level_values(0) == sym]
                    if not sym_df.empty:
                        result[sym] = float(sym_df["close"].iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass

    return result


def _compute_score(avg_return: float, win_rate: float, sharpe: float, n_trades: int) -> float:
    """Combine metrics into a 0–100 score."""
    # return_score: tanh maps any return to (-1,1), then shift to (0,1)
    return_score = (math.tanh(avg_return * 2) + 1) / 2

    win_score = win_rate  # already 0–1

    # sharpe_score: clamp to [0, 3], normalize
    sharpe_score = max(0.0, min(sharpe, 3.0)) / 3.0

    # trade_count_score: saturates at 30 trades
    trade_score = min(n_trades / 30.0, 1.0)

    raw = (0.40 * return_score +
           0.30 * win_score +
           0.20 * sharpe_score +
           0.10 * trade_score)
    return round(raw * 100, 1)


def _fetch_historical_prices_bulk(tickers: list[str], start: datetime, end: datetime) -> dict[tuple, float]:
    """
    Fetch 12 months of daily bars for all tickers in batches of 100.
    Returns {(ticker, 'YYYY-MM-DD') -> close_price} for fast lookup.
    """
    lookup: dict[tuple, float] = {}
    batch_size = 100

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
            bars = _get_data_client().get_stock_bars(req)
            df = bars.df
            if df.empty:
                continue

            # MultiIndex: (symbol, timestamp) -> close
            for sym in batch:
                try:
                    if "symbol" in df.index.names:
                        sym_df = df.xs(sym, level="symbol")
                    else:
                        sym_df = df[df.index.get_level_values(0) == sym]
                    for ts, row in sym_df.iterrows():
                        date_str = str(ts)[:10]
                        lookup[(sym, date_str)] = float(row["close"])
                except Exception:
                    pass
        except Exception:
            pass

    return lookup


def _lookup_close(lookup: dict, ticker: str, date_str: str) -> float | None:
    """Find the close price for ticker on or after date_str (up to 7 days later)."""
    target = datetime.fromisoformat(date_str).date()
    for offset in range(8):
        candidate = (target + timedelta(days=offset)).isoformat()
        price = lookup.get((ticker, candidate))
        if price:
            return price
    return None


def rank_politicians(trades: list[dict]) -> list[dict]:
    """
    Score every politician by their 12-month buy performance.
    Returns list sorted by score descending.
    """
    cutoff_12m = datetime.now(timezone.utc) - timedelta(days=365)

    buys_by_pol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t["tx_type"] != "buy":
            continue
        if not t.get("tx_date"):
            continue
        tx_dt = datetime.fromisoformat(t["tx_date"][:10]).replace(tzinfo=timezone.utc)
        if tx_dt < cutoff_12m:
            continue
        buys_by_pol[t["pol_id"]].append(t)

    all_tickers = list({t["ticker"] for pol_buys in buys_by_pol.values() for t in pol_buys})
    print(f"[analyzer] Fetching current prices for {len(all_tickers)} tickers (batches of 100)...")
    current_prices = _fetch_current_prices(all_tickers)
    print(f"[analyzer] Got {len(current_prices)} current prices.")

    # Fetch 12 months of daily history for all tickers in bulk — avoids 5000+ individual calls
    hist_end = datetime.now(timezone.utc)
    hist_start = cutoff_12m - timedelta(days=7)
    print(f"[analyzer] Fetching 12-month price history for {len(all_tickers)} tickers...")
    hist_lookup = _fetch_historical_prices_bulk(all_tickers, hist_start, hist_end)
    print(f"[analyzer] Got {len(hist_lookup)} (ticker, date) price points.")

    rankings = []
    for pol_id, pol_buys in buys_by_pol.items():
        if len(pol_buys) < MIN_TRADES:
            continue

        per_trade_returns: list[float] = []
        total_weight = 0.0
        weighted_return = 0.0

        for trade in pol_buys:
            ticker = trade["ticker"]
            current = current_prices.get(ticker)
            if current is None:
                continue
            buy_price = _lookup_close(hist_lookup, ticker, trade["tx_date"])
            if not buy_price or buy_price == 0:
                continue

            ret = (current - buy_price) / buy_price
            weight = trade.get("value", 8000)
            per_trade_returns.append(ret)
            weighted_return += ret * weight
            total_weight += weight

        if not per_trade_returns or total_weight == 0:
            continue

        avg_return = weighted_return / total_weight
        win_rate = sum(1 for r in per_trade_returns if r > 0) / len(per_trade_returns)
        n = len(per_trade_returns)

        if n > 1:
            mean_r = sum(per_trade_returns) / n
            variance = sum((r - mean_r) ** 2 for r in per_trade_returns) / (n - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0
            sharpe = (mean_r / std_r) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        score = _compute_score(avg_return, win_rate, sharpe, n)

        rankings.append({
            "pol_id":    pol_id,
            "pol_name":  pol_buys[0]["pol_name"],
            "score":     score,
            "return":    avg_return,
            "win_rate":  win_rate,
            "sharpe":    round(sharpe, 2),
            "n_trades":  len(pol_buys),
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings


def get_open_positions(pol_id: str, trades: list[dict]) -> list[dict]:
    """
    Estimate current open positions: net buys (buys − sells) per ticker.
    Returns list of {ticker, net_dollars, sector} sorted by size, capped at MAX_POSITIONS.
    """
    net: dict[str, float] = defaultdict(float)
    sector_map: dict[str, str] = {}

    for t in trades:
        if t["pol_id"] != pol_id:
            continue
        sector_map[t["ticker"]] = t.get("sector", "unknown")
        dollars = t.get("value", 8000)
        if t["tx_type"] == "buy":
            net[t["ticker"]] += dollars
        elif t["tx_type"] == "sell":
            net[t["ticker"]] -= dollars

    open_pos = [
        {"ticker": tkr, "net_dollars": amt, "sector": sector_map.get(tkr, "unknown")}
        for tkr, amt in net.items()
        if amt > 0
    ]
    open_pos.sort(key=lambda x: x["net_dollars"], reverse=True)
    open_pos = open_pos[:MAX_POSITIONS]

    # Enrich sectors from yfinance cache — Capitol Trades sector data is often null
    live_sectors = _get_sectors([p["ticker"] for p in open_pos])
    for p in open_pos:
        sec = live_sectors.get(p["ticker"])
        if sec and sec != "unknown":
            p["sector"] = sec

    return open_pos
