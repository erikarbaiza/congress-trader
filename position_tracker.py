"""
Tracks entry date per ticker so positions can be exited after HOLD_DAYS.
Persisted in position_entries.json (committed to git like trades_cache.pkl).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TRACKER_FILE = Path(__file__).parent / "position_entries.json"


def _load() -> dict[str, str]:
    if _TRACKER_FILE.exists():
        try:
            return json.loads(_TRACKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict[str, str]) -> None:
    _TRACKER_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def record_entry(ticker: str) -> None:
    data = _load()
    if ticker not in data:
        data[ticker] = datetime.now(timezone.utc).isoformat()
        _save(data)


def remove(ticker: str) -> None:
    data = _load()
    if ticker in data:
        del data[ticker]
        _save(data)


def get_stale_tickers(hold_days: int) -> list[str]:
    """Return tickers whose entry is older than hold_days calendar days."""
    data  = _load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=hold_days)
    return [t for t, iso in data.items() if datetime.fromisoformat(iso) < cutoff]


def get_all() -> dict[str, str]:
    return _load()
