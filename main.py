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
from config import TOP_PERFORMERS, MAX_POSITION_PCT


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
    leaders = rankings[:TOP_PERFORMERS]
    names = ", ".join(r["pol_name"] for r in leaders)
    print(f"[analyzer] Top {TOP_PERFORMERS}: {names}")

    # 3. Merge open positions from top N performers
    print("[analyzer] Resolving open positions…")
    merged: dict[str, dict] = {}
    for leader in leaders:
        for pos in get_open_positions(leader["pol_id"], trades):
            ticker = pos["ticker"]
            if ticker in merged:
                merged[ticker]["net_dollars"] += pos["net_dollars"]
            else:
                merged[ticker] = pos.copy()
    target_positions = sorted(merged.values(), key=lambda x: x["net_dollars"], reverse=True)
    print(f"[analyzer] {len(target_positions)} open positions to mirror (from {TOP_PERFORMERS} politicians).")

    # 4. Fetch equity and annotate each position with its estimated allocation
    equity = get_account_equity()
    print(f"[trader] Account equity: ${equity:,.2f}")

    if target_positions:
        equal_share = equity / len(target_positions)
        max_pos_value = equity * MAX_POSITION_PCT
        for pos in target_positions:
            pos["scaled_value"] = round(min(equal_share, max_pos_value), 2)

    if not target_positions:
        print("[main] No open positions found — no orders will be placed.")
    else:
        # 5. Mirror positions in the paper account
        print("[trader] Mirroring positions…")
        trade_log = mirror_positions(target_positions)
        for entry in trade_log:
            print(f"  {entry['action']:12} {entry['ticker']:8} qty={entry['qty']}  {entry['reason']}")

    # 6. Log daily snapshot to results.csv and get metrics for email
    metrics = log_daily(
        top_performer=top,
        account_equity=equity,
        target_positions=target_positions,
        trade_log=trade_log if target_positions else [],
    )

    # 7. Send email summary (includes daily metrics table)
    print("[emailer] Sending summary…")
    send_summary(
        top_performer=top,
        rankings=rankings,
        target_positions=target_positions,
        trade_log=trade_log if target_positions else [],
        account_equity=equity,
        prev_equity=equity,
        metrics=metrics,
    )

    print("[main] Done.")


if __name__ == "__main__":
    run()
