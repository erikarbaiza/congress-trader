"""
Per-trade journal for PEAD: entry metadata at buy, one CSV row at close.
Needed to calibrate stop-loss, sizing and hold days with real data.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

_META_FILE   = Path(__file__).parent / "trade_meta.json"
_CLOSED_FILE = Path(__file__).parent / "closed_trades.csv"

_HEADERS = [
    "ticker", "entry_date", "exit_date", "days_held",
    "entry_price", "exit_price", "return_pct",
    "surprise_pct", "position_pct", "sector", "exit_reason", "exit_price_source",
]


def _load_meta() -> dict:
    if _META_FILE.exists():
        try:
            return json.loads(_META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def record_open(ticker: str, beat: dict, position_pct: float) -> None:
    meta = _load_meta()
    meta[ticker] = {
        "entry_date":   datetime.now(timezone.utc).isoformat(),
        "surprise_pct": beat.get("surprise_pct"),
        "sector":       beat.get("sector", "unknown"),
        "position_pct": round(position_pct, 4),
    }
    _META_FILE.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{h: r.get(h) or "" for h in _HEADERS} for r in csv.DictReader(f)]


def _write_rows(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _ensure_schema(path: Path) -> None:
    """Rewrite a CSV that predates a header change so appended rows line up."""
    if not path.exists():
        return
    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])
    if header != _HEADERS:
        _write_rows(path, _read_rows(path))


def record_close(ticker: str, entry_price: float, exit_price: float, reason: str) -> None:
    """Log a close at the last seen price; reconcile_exit_prices() swaps in the real fill later."""
    _ensure_schema(_CLOSED_FILE)
    meta = _load_meta()
    m = meta.pop(ticker, {})
    _META_FILE.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    now = datetime.now(timezone.utc)
    entry_iso = m.get("entry_date")
    days_held = (now - datetime.fromisoformat(entry_iso)).days if entry_iso else ""
    ret = (exit_price - entry_price) / entry_price if entry_price else ""

    row = {
        "ticker":       ticker,
        "entry_date":   entry_iso[:10] if entry_iso else "",
        "exit_date":    now.strftime("%Y-%m-%d"),
        "days_held":    days_held,
        "entry_price":  f"{entry_price:.2f}",
        "exit_price":   f"{exit_price:.2f}",
        "return_pct":   f"{ret:.4f}" if ret != "" else "",
        "surprise_pct": m.get("surprise_pct", ""),
        "position_pct": m.get("position_pct", ""),
        "sector":       m.get("sector", ""),
        "exit_reason":  reason,
        "exit_price_source": "estimate",
    }
    new_file = not _CLOSED_FILE.exists()
    with open(_CLOSED_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADERS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _first_sell_fill(client, ticker: str, since: datetime):
    """Earliest filled SELL order for `ticker` at or after `since`, or None."""
    from alpaca.trading.enums import OrderSide, QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.CLOSED, symbols=[ticker], side=OrderSide.SELL,
        after=since, limit=100,
    ))
    fills = [o for o in orders if o.filled_at and o.filled_avg_price]
    return min(fills, key=lambda o: o.filled_at) if fills else None


def reconcile_exit_prices(client=None, path: Path = _CLOSED_FILE) -> int:
    """Replace estimated exit prices with the real Alpaca fill; returns rows updated.

    close_position() only submits the order, so the journal first logs the last quote.
    Rows whose order has not filled yet stay as 'estimate' and are retried next run.
    """
    if client is None:
        from trader import _get_trading_client
        client = _get_trading_client()

    _ensure_schema(path)
    rows = _read_rows(path)
    updated = 0
    for row in rows:
        if row["exit_price_source"] == "fill" or not row["exit_date"]:
            continue
        since = datetime.fromisoformat(row["exit_date"]).replace(tzinfo=timezone.utc)
        order = _first_sell_fill(client, row["ticker"], since)
        if order is None:
            continue
        fill = float(order.filled_avg_price)
        entry = float(row["entry_price"])
        row["exit_price"]  = f"{fill:.2f}"
        row["return_pct"]  = f"{(fill - entry) / entry:.4f}" if entry else ""
        row["exit_date"]   = order.filled_at.strftime("%Y-%m-%d")
        if row["entry_date"]:
            row["days_held"] = (order.filled_at.date() - datetime.fromisoformat(row["entry_date"]).date()).days
        row["exit_price_source"] = "fill"
        updated += 1
    if updated:
        _write_rows(path, rows)
    return updated
