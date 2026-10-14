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
    "surprise_pct", "position_pct", "sector", "exit_reason",
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


def record_close(ticker: str, entry_price: float, exit_price: float, reason: str) -> None:
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
    }
    new_file = not _CLOSED_FILE.exists()
    with open(_CLOSED_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADERS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
