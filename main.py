"""
Entry point — runs two strategies each weekday at 15:35 UTC:

  1. PEAD (Post-Earnings Announcement Drift) — live trading on Alpaca
       a. Exit stale / stopped positions
       b. Scan S&P 500 earnings beats
       c. Buy top beats
       d. Email summary + log to results_earnings.csv

  2. Congressional monitor — analysis only, no trading (would conflict with PEAD)
       a. Fetch Capitol Trades data
       b. Rank politicians by best_ret
       c. Log top performer snapshot to results.csv for dashboard tracking
"""

import sys
from datetime import datetime

from earnings_scanner import get_earnings_beats
from position_tracker import get_stale_tickers, record_entry, remove, get_all
from trader import (
    get_account_equity, get_current_positions,
    buy_earnings_position, exit_position, check_earnings_stops, calc_position_pct,
)
from emailer import send_earnings_summary, send_sell_alert
from logger import log_earnings_daily, log_daily
from scraper import get_stock_trades
from analyzer import rank_politicians, get_open_positions, compute_bh_return
from collections import defaultdict
from analyzer import _get_sectors
from config import HOLD_DAYS, MAX_POSITIONS, MAX_SECTOR_PCT, MAX_INVESTED_PCT, TOP_PERFORMERS, DAYS_BACK_BEATS
from trade_journal import record_open, record_close


def _journal_close(ticker: str, pos: dict, reason: str) -> None:
    try:
        record_close(ticker, pos["avg_entry_price"], pos["current_price"], reason)
    except Exception as e:
        print(f"[main] trade journal failed for {ticker} (non-critical): {e}")


def run() -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] CongressTrader — Earnings Momentum starting…")

    equity  = get_account_equity()
    current = get_current_positions()
    log: list[dict] = []

    # 1. Exit positions past their HOLD_DAYS window
    stale = get_stale_tickers(HOLD_DAYS)
    for ticker in stale:
        if ticker in current:
            result = exit_position(ticker, reason=f"PEAD window closed ({HOLD_DAYS}d elapsed)")
            log.append(result)
            if result["action"] == "SELL":
                _journal_close(ticker, current[ticker], result["reason"])
                remove(ticker)
                print(f"  EXIT {ticker} — hold period elapsed")
            else:
                print(f"  EXIT {ticker} FAILED — kept in tracker, retry next run: {result['reason']}")

    # 2. Stop-loss sweep
    current = get_current_positions()
    stops   = check_earnings_stops(current)
    for entry in stops:
        log.append(entry)
        if entry["action"] == "STOP_LOSS":
            _journal_close(entry["ticker"], current[entry["ticker"]], entry["reason"])
            remove(entry["ticker"])
        print(f"  {entry['action']} {entry['ticker']} — {entry['reason']}")

    # 2.5. Sell alert — fires immediately when positions close, before the daily summary
    _sell_actions = {"SELL", "STOP_LOSS", "TAKE_PROFIT"}
    sells_today = [e for e in log if e.get("action") in _sell_actions]
    if sells_today:
        try:
            send_sell_alert(sells_today, get_account_equity())
        except Exception as _e:
            print(f"[main] sell alert failed (non-critical): {_e}")

    # 3. Count available position slots
    current = get_current_positions()
    slots   = MAX_POSITIONS - len(current)
    print(f"[main] {len(current)} positions open, {slots} slots available")

    # 4. Scan for earnings beats
    beats = get_earnings_beats(days_back=DAYS_BACK_BEATS)

    # 5. Buy new beats — gross-exposure cap and sector cap count already-open positions
    new_buys: list[dict] = []
    sector_capital: dict[str, float] = defaultdict(float)
    invested = sum(p["market_value"] for p in current.values())
    if current:
        for t, sec in _get_sectors(list(current)).items():
            sector_capital[sec] += current[t]["market_value"]
    for beat in beats:
        if slots <= 0:
            break
        if beat["ticker"] in current:
            continue

        sector = beat.get("sector") or "unknown"
        equity = get_account_equity()
        room = equity * MAX_INVESTED_PCT - invested
        if room <= 0:
            log.append({
                "action": "SKIP", "ticker": beat["ticker"], "qty": None,
                "reason": f"exposure cap: {invested/equity:.0%} invested of {MAX_INVESTED_PCT:.0%} limit",
            })
            print(f"  SKIP {beat['ticker']:6} — exposure cap ({invested/equity:.0%} invested)")
            continue

        intended_value = min(equity * calc_position_pct(beat.get("surprise_pct", 0), beat.get("market_cap")), room)
        if sector_capital[sector] + intended_value > equity * MAX_SECTOR_PCT:
            log.append({
                "action": "SKIP", "ticker": beat["ticker"], "qty": None,
                "reason": f"sector cap: {sector} at {sector_capital[sector]/equity:.0%} (+{intended_value/equity:.0%}) of {MAX_SECTOR_PCT:.0%} limit",
            })
            print(f"  SKIP {beat['ticker']:6} — sector cap ({sector})")
            continue

        result = buy_earnings_position(beat["ticker"], beat, equity, slots, room=room)
        log.append(result)
        if result["action"] == "BUY":
            record_entry(beat["ticker"])
            try:
                record_open(beat["ticker"], beat, result["value"] / equity)
            except Exception as e:
                print(f"[main] trade journal open failed (non-critical): {e}")
            new_buys.append(beat)
            slots -= 1
            invested += result["value"]
            sector_capital[sector] += result["value"]
            print(f"  BUY  {beat['ticker']:6} — EPS +{beat['surprise_pct']:.1f}% ({sector})")

    # 6. Email + log
    equity   = get_account_equity()
    entries  = get_all()

    send_earnings_summary(
        beats_bought=new_buys,
        beats_available=beats,
        trade_log=log,
        account_equity=equity,
        position_entries=entries,
    )
    log_earnings_daily(
        account_equity=equity,
        n_positions=len(get_current_positions()),
        trade_log=log,
        beats_found=len(beats),
    )

    print("[main] PEAD done.")

    # ── Congressional monitor (no trading — would conflict with PEAD) ──────────
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M}] Congressional analysis starting…")
    try:
        trades   = get_stock_trades()
        rankings = rank_politicians(trades)

        if rankings:
            top       = rankings[0]
            positions = get_open_positions(top["pol_id"], trades)
            bh_return = compute_bh_return(positions)
            log_daily(
                top_performer=top,
                account_equity=equity,   # PEAD account equity — shared baseline
                target_positions=positions,
                trade_log=[],            # monitor only, no orders placed
                bh_return=bh_return,
            )
            print(f"[main] Congressional top: {top['pol_name']} ({top['return']:+.1%} return, {top['win_rate']:.0%} wr)")
        else:
            print("[main] Congressional: no qualifying politicians found.")
    except Exception as e:
        print(f"[main] Congressional analysis failed: {e}")

    print("[main] Done.")


if __name__ == "__main__":
    run()
