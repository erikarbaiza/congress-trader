"""
Logs daily results to results.csv for portfolio tracking and README charts.
Tracks equity curve, portfolio return vs S&P 500 (SPY), and order history.
"""

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from analyzer import _fetch_current_prices

RESULTS_FILE = Path(__file__).parent / "results.csv"
BASELINE_FILE = Path(__file__).parent / "baseline.json"

CSV_HEADERS = [
    "date",
    "top_performer",
    "score",
    "return_12m_pct",
    "win_rate",
    "trade_rr",
    "account_equity",
    "initial_equity",
    "portfolio_return_pct",
    "bh_return_pct",
    "spy_price",
    "spy_baseline",
    "spy_return_pct",
    "positions_open",
    "orders_executed",
]


def _get_spy_price() -> float:
    prices = _fetch_current_prices(["SPY"])
    return prices.get("SPY", 0.0)


def _load_or_create_baseline(equity: float, spy_price: float) -> tuple[float, float]:
    """Return (initial_equity, spy_baseline). Creates baseline.json on first run."""
    if not BASELINE_FILE.exists():
        baseline = {"initial_equity": equity, "spy_baseline": spy_price}
        with open(BASELINE_FILE, "w") as f:
            json.dump(baseline, f)
        print(f"[logger] Baseline created — equity=${equity:,.2f}, SPY=${spy_price:.2f}")
        return equity, spy_price

    with open(BASELINE_FILE) as f:
        data = json.load(f)
    return data["initial_equity"], data["spy_baseline"]


def _migrate_csv_if_needed() -> None:
    """Archive the old CSV if it was created without the current headers."""
    if not RESULTS_FILE.exists():
        return
    with open(RESULTS_FILE, "r", encoding="utf-8") as f:
        header = f.readline()
    if "bh_return_pct" not in header:
        dest = RESULTS_FILE.parent / "results_legacy.csv"
        shutil.move(str(RESULTS_FILE), str(dest))
        print(f"[logger] Migrated old CSV to {dest.name} — starting fresh with new schema.")


def log_daily(
    top_performer: dict,
    account_equity: float,
    target_positions: list[dict],
    trade_log: list[dict],
    bh_return: float | None = None,
) -> dict:
    """Log daily snapshot to CSV. Returns metrics dict for use in email summary."""
    _migrate_csv_if_needed()
    spy_price = _get_spy_price()
    initial_equity, spy_baseline = _load_or_create_baseline(account_equity, spy_price)

    portfolio_return = (account_equity - initial_equity) / initial_equity if initial_equity else 0
    spy_return = (spy_price - spy_baseline) / spy_baseline if spy_baseline else 0
    orders_executed = sum(1 for o in trade_log if o["action"] in ("BUY", "SELL"))

    row = {
        "date":                 datetime.now().strftime("%Y-%m-%d"),
        "top_performer":        top_performer["pol_name"],
        "score":                top_performer.get("score", ""),
        "return_12m_pct":       f"{top_performer['return']:.4f}",
        "win_rate":             f"{top_performer.get('win_rate', 0):.4f}",
        "trade_rr":             f"{top_performer.get('sharpe', 0):.2f}",
        "bh_return_pct":        f"{bh_return:.4f}" if bh_return is not None else "",
        "account_equity":       f"{account_equity:.2f}",
        "initial_equity":       f"{initial_equity:.2f}",
        "portfolio_return_pct": f"{portfolio_return:.4f}",
        "spy_price":            f"{spy_price:.2f}",
        "spy_baseline":         f"{spy_baseline:.2f}",
        "spy_return_pct":       f"{spy_return:.4f}",
        "positions_open":       len(target_positions),
        "orders_executed":      orders_executed,
    }

    file_exists = RESULTS_FILE.exists()
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"[logger] Logged — portfolio {portfolio_return:+.2%} | SPY {spy_return:+.2%} | orders={orders_executed}")

    return {
        "date":               row["date"],
        "initial_equity":     initial_equity,
        "portfolio_return":   portfolio_return,
        "spy_price":          spy_price,
        "spy_baseline":       spy_baseline,
        "spy_return":         spy_return,
    }
