"""
Entry point — Post-Earnings Announcement Drift (PEAD) strategy.

Each weekday at 15:35 (Spain):
  1. Exit positions held longer than HOLD_DAYS or stopped out
  2. Scan S&P 500 for today's earnings beats
  3. Buy the top beats with available slots
  4. Email summary + log
"""

import sys
from datetime import datetime

from earnings_scanner import get_earnings_beats
from position_tracker import get_stale_tickers, record_entry, remove, get_all
from trader import (
    get_account_equity, get_current_positions,
    buy_earnings_position, exit_position, check_earnings_stops,
)
from emailer import send_earnings_summary
from logger import log_earnings_daily
from config import HOLD_DAYS, MAX_POSITIONS


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
            remove(ticker)
            print(f"  EXIT {ticker} — hold period elapsed")

    # 2. Stop-loss sweep
    current = get_current_positions()
    stops   = check_earnings_stops(current)
    for entry in stops:
        log.append(entry)
        remove(entry["ticker"])
        print(f"  STOP {entry['ticker']} — {entry['reason']}")

    # 3. Count available position slots
    current = get_current_positions()
    slots   = MAX_POSITIONS - len(current)
    print(f"[main] {len(current)} positions open, {slots} slots available")

    # 4. Scan for earnings beats
    beats = get_earnings_beats(days_back=2)

    # 5. Buy new beats
    new_buys: list[dict] = []
    for beat in beats:
        if slots <= 0:
            break
        if beat["ticker"] in current:
            continue
        equity = get_account_equity()
        result = buy_earnings_position(beat["ticker"], beat, equity, slots)
        log.append(result)
        if result["action"] == "BUY":
            record_entry(beat["ticker"])
            new_buys.append(beat)
            slots -= 1
            print(f"  BUY  {beat['ticker']:6} — EPS +{beat['surprise_pct']:.1f}%")

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

    print("[main] Done.")


if __name__ == "__main__":
    run()
