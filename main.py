"""
Entry point — run once at market open each weekday.
Orchestrates: scrape → rank → identify top performer → mirror → email.
"""

import sys
from datetime import datetime

from scraper import get_stock_trades
from analyzer import rank_politicians, get_open_positions
from trader import mirror_positions, get_account_equity
from emailer import send_summary
from logger import log_daily


def run() -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] CongressTrader starting…")

    # 1. Fetch all stock trades from the past ~13 months
    print("[scraper] Fetching trades…")
    trades = get_stock_trades(days_back=365)
    print(f"[scraper] {len(trades)} stock trades fetched.")

    if not trades:
        print("[main] No trades found — aborting.")
        sys.exit(1)

    # 2. Rank politicians by 12-month weighted return
    print("[analyzer] Ranking politicians…")
    rankings = rank_politicians(trades)
    if not rankings:
        print("[main] Could not rank any politician — check MIN_TRADES and field names.")
        sys.exit(1)

    top = rankings[0]
    print(f"[analyzer] Top performer: {top['pol_name']}  return={top['return']:.2%}  trades={top['n_trades']}")

    # 3. Determine their estimated open positions
    print("[analyzer] Resolving open positions…")
    target_positions = get_open_positions(top["pol_id"], trades)
    print(f"[analyzer] {len(target_positions)} open positions to mirror.")

    if not target_positions:
        print("[main] No open positions found — no orders will be placed.")
    else:
        # 4. Mirror positions in the paper account
        print("[trader] Mirroring positions…")
        trade_log = mirror_positions(target_positions)
        for entry in trade_log:
            print(f"  {entry['action']:12} {entry['ticker']:8} qty={entry['qty']}  {entry['reason']}")

    # 5. Send email summary
    prev_equity = get_account_equity()  # snapshot before any pending fills settle
    equity = prev_equity
    print(f"[trader] Account equity: ${equity:,.2f}")
    print("[emailer] Sending summary…")
    send_summary(
        top_performer=top,
        rankings=rankings,
        target_positions=target_positions,
        trade_log=trade_log if target_positions else [],
        account_equity=equity,
        prev_equity=equity,
    )

    # 6. Log daily snapshot to results.csv for tracking vs S&P 500
    log_daily(
        top_performer=top,
        account_equity=equity,
        target_positions=target_positions,
        trade_log=trade_log if target_positions else [],
    )

    print("[main] Done.")


if __name__ == "__main__":
    run()
