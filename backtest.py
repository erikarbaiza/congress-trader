"""
Walk-forward backtest for CongressTrader.

At each month T (point-in-time), ranks politicians using only trades
disclosed before T, takes top-N open positions, simulates the following
month, then advances.

Usage:
    python backtest.py

Output: backtest_results.csv  (date, portfolio_value, bot_cumulative,
                                spy_price, spy_cumulative, monthly_return,
                                spy_monthly, top_performer, n_positions)

NOTE: requires >= 13 months of cached trade data for meaningful results.
If you only have 12 months, the first usable backtest period won't start
until the ranking window is satisfied. Run the scraper with a larger
days_back (e.g. 730) to get more history.
"""

import csv
import math
import pickle
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer import (
    _fetch_historical_prices_bulk,
    _lookup_close,
    _compute_score,
)
from config import MIN_TRADES, TOP_PERFORMERS, MAX_POSITIONS

CACHE_FILE   = Path(__file__).parent / "trades_cache.pkl"
OUTPUT_FILE  = Path(__file__).parent / "backtest_results.csv"
RANKING_DAYS = 365   # how many days back each ranking window looks
STEP_DAYS    = 30    # walk-forward step in days


def _load_trades() -> list[dict]:
    if not CACHE_FILE.exists():
        raise FileNotFoundError(
            "trades_cache.pkl not found. Run the bot at least once to populate the cache."
        )
    with open(CACHE_FILE, "rb") as f:
        return pickle.load(f)


def _rank_at_date(trades: list[dict], as_of: datetime, hist_lookup: dict) -> list[dict]:
    """Point-in-time ranking: only uses trades whose pub_date <= as_of."""
    cutoff = as_of - timedelta(days=RANKING_DAYS)

    buys_by_pol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t["tx_type"] != "buy":
            continue
        pub = t.get("pub_date") or t.get("tx_date", "")
        if not pub:
            continue
        pub_dt = datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc)
        if pub_dt > as_of or pub_dt < cutoff:
            continue
        buys_by_pol[t["pol_id"]].append(t)

    rankings = []
    as_of_str = as_of.strftime("%Y-%m-%d")

    for pol_id, buys in buys_by_pol.items():
        if len(buys) < MIN_TRADES:
            continue

        per_trade_returns, weighted_return, total_weight = [], 0.0, 0.0
        for trade in buys:
            ticker     = trade["ticker"]
            entry_date = (trade.get("pub_date") or trade["tx_date"])[:10]
            buy_price  = _lookup_close(hist_lookup, ticker, entry_date)
            cur_price  = _lookup_close(hist_lookup, ticker, as_of_str)
            if not buy_price or not cur_price or buy_price == 0:
                continue
            ret    = (cur_price - buy_price) / buy_price
            weight = trade.get("value", 8000)
            per_trade_returns.append(ret)
            weighted_return += ret * weight
            total_weight    += weight

        if not per_trade_returns or total_weight == 0:
            continue

        avg_return = weighted_return / total_weight
        win_rate   = sum(1 for r in per_trade_returns if r > 0) / len(per_trade_returns)
        n          = len(per_trade_returns)
        if n > 1:
            mean_r   = sum(per_trade_returns) / n
            variance = sum((r - mean_r) ** 2 for r in per_trade_returns) / (n - 1)
            std_r    = math.sqrt(variance) if variance > 0 else 0
            sharpe   = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        rankings.append({
            "pol_id":   pol_id,
            "pol_name": buys[0]["pol_name"],
            "score":    _compute_score(avg_return, win_rate, sharpe, n),
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings


def _open_positions_at(pol_id: str, trades: list[dict], as_of: datetime) -> list[str]:
    """Tickers with net buys > 0 as of as_of (pub_date gating)."""
    net: dict[str, float] = defaultdict(float)
    for t in trades:
        if t["pol_id"] != pol_id:
            continue
        pub = t.get("pub_date") or t.get("tx_date", "")
        if not pub:
            continue
        if datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc) > as_of:
            continue
        val = t.get("value", 8000)
        if t["tx_type"] == "buy":
            net[t["ticker"]] += val
        elif t["tx_type"] == "sell":
            net[t["ticker"]] -= val
    return [tkr for tkr, amt in net.items() if amt > 0][:MAX_POSITIONS]


def run() -> None:
    print("[backtest] Loading trades from cache…")
    trades = _load_trades()
    print(f"[backtest] {len(trades)} trades loaded.")

    pub_dates = []
    for t in trades:
        raw = t.get("pub_date") or t.get("tx_date", "")
        if raw:
            pub_dates.append(datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc))
    if not pub_dates:
        print("[backtest] No dated trades found.")
        return

    data_start = min(pub_dates)
    data_end   = max(pub_dates)
    print(f"[backtest] Data range: {data_start.date()} → {data_end.date()}")

    # First valid backtest date = data_start + ranking window
    bt_start = data_start + timedelta(days=RANKING_DAYS)
    if bt_start >= data_end - timedelta(days=STEP_DAYS):
        print(
            f"[backtest] Not enough data for a meaningful walk-forward.\n"
            f"  Need > {RANKING_DAYS + STEP_DAYS} days; have {(data_end - data_start).days} days.\n"
            f"  Tip: increase days_back in get_stock_trades() to 730+ and re-run the scraper."
        )
        return

    # Fetch all historical prices upfront (one big batch call)
    all_tickers = list({t["ticker"] for t in trades} | {"SPY"})
    hist_start  = data_start - timedelta(days=7)
    hist_end    = data_end   + timedelta(days=7)
    print(f"[backtest] Fetching price history for {len(all_tickers)} tickers…")
    hist_lookup = _fetch_historical_prices_bulk(all_tickers, hist_start, hist_end)
    print(f"[backtest] {len(hist_lookup)} price points fetched.")

    spy_baseline = _lookup_close(hist_lookup, "SPY", bt_start.strftime("%Y-%m-%d"))
    portfolio    = 100_000.0
    results      = []

    current = bt_start
    while current <= data_end - timedelta(days=STEP_DAYS):
        next_dt = current + timedelta(days=STEP_DAYS)

        rankings = _rank_at_date(trades, current, hist_lookup)
        if not rankings:
            current = next_dt
            continue

        leaders = rankings[:TOP_PERFORMERS]
        merged: dict[str, int] = {}
        for leader in leaders:
            for tkr in _open_positions_at(leader["pol_id"], trades, current):
                merged[tkr] = merged.get(tkr, 0) + 1

        if not merged:
            current = next_dt
            continue

        cur_str  = current.strftime("%Y-%m-%d")
        next_str = next_dt.strftime("%Y-%m-%d")

        monthly_returns = []
        for tkr in merged:
            p0 = _lookup_close(hist_lookup, tkr, cur_str)
            p1 = _lookup_close(hist_lookup, tkr, next_str)
            if p0 and p1 and p0 != 0:
                monthly_returns.append((p1 - p0) / p0)

        if not monthly_returns:
            current = next_dt
            continue

        monthly_return = sum(monthly_returns) / len(monthly_returns)
        portfolio     *= (1 + monthly_return)

        spy0 = _lookup_close(hist_lookup, "SPY", cur_str)
        spy1 = _lookup_close(hist_lookup, "SPY", next_str)
        spy_monthly  = (spy1 - spy0) / spy0 if spy0 and spy1 else 0.0
        spy_now      = _lookup_close(hist_lookup, "SPY", next_str) or 0.0
        spy_cum      = (spy_now - spy_baseline) / spy_baseline if spy_baseline else 0.0
        bot_cum      = (portfolio - 100_000) / 100_000

        row = {
            "date":            cur_str,
            "top_performer":   leaders[0]["pol_name"],
            "n_positions":     len(merged),
            "portfolio_value": f"{portfolio:.2f}",
            "bot_cumulative":  f"{bot_cum:.4f}",
            "spy_price":       f"{spy_now:.2f}",
            "spy_cumulative":  f"{spy_cum:.4f}",
            "monthly_return":  f"{monthly_return:.4f}",
            "spy_monthly":     f"{spy_monthly:.4f}",
            "bot_vs_spy":      f"{bot_cum - spy_cum:.4f}",
        }
        results.append(row)
        print(
            f"  {cur_str}  portfolio=${portfolio:>10,.0f} ({monthly_return:+.2%})  "
            f"SPY {spy_monthly:+.2%}  top={leaders[0]['pol_name']}"
        )
        current = next_dt

    if not results:
        print("[backtest] No results generated — try increasing the data range.")
        return

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    final    = float(results[-1]["portfolio_value"])
    bot_tot  = (final - 100_000) / 100_000
    spy_tot  = float(results[-1]["spy_cumulative"])
    print(f"\n[backtest] Done — {len(results)} periods | {OUTPUT_FILE.name}")
    print(f"  Bot total return : {bot_tot:+.2%}")
    print(f"  SPY total return : {spy_tot:+.2%}")
    print(f"  Bot vs SPY       : {bot_tot - spy_tot:+.2%}")


if __name__ == "__main__":
    run()
