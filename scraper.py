"""
Fetches congressional trade disclosures from Capitol Trades by extracting
the RSC (React Server Components) payload embedded in the server-rendered HTML.

Field names confirmed from live HTML on 2026-06-14:
  Root:      _txId, _politicianId, txDate, txType, pubDate, value, owner, price
  issuer:    issuerTicker (e.g. "AAPL:US"), issuerName, sector
  politician: firstName, lastName, party, chamber
"""

import re
import json
import time
import pickle
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests

CACHE_FILE = Path(__file__).parent / "trades_cache.pkl"

BASE_URL = "https://www.capitoltrades.com/trades"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_page_html(page: int, days_back: int, retries: int = 4) -> str:
    params = f"txDate={days_back}d" + (f"&page={page}" if page > 1 else "")
    url = f"{BASE_URL}?{params}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries - 1:
                wait = 5 * (2 ** attempt)   # 5s, 10s, 20s
                print(f"[scraper] Page {page} error ({e.__class__.__name__}), retry in {wait}s…")
                time.sleep(wait)
            else:
                raise


def _unescape_rsc(html: str) -> str:
    """
    Remove one level of JS string escaping from the RSC payload.
    The Next.js RSC chunks embed JSON inside JS strings, so `"` → `\"`
    in the raw HTML.  After unescaping, we get normal JSON.
    """
    return html.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')


def _extract_trades_from_html(html: str) -> list[dict]:
    """
    Find all trade JSON objects embedded in the RSC payload.
    Trades are identified by the presence of `"txType":"buy"/"sell"`.
    We locate each occurrence and extract the enclosing JSON object
    by counting braces.
    """
    unescaped = _unescape_rsc(html)
    trades: list[dict] = []
    seen: set = set()

    for m in re.finditer(r'"txType"\s*:\s*"(?:buy|sell)"', unescaped):
        pos = m.start()

        # Walk backwards to find the opening { of this object
        depth = 0
        start = -1
        for i in range(pos - 1, max(0, pos - 8000), -1):
            c = unescaped[i]
            if c == '}':
                depth += 1
            elif c == '{':
                if depth == 0:
                    start = i
                    break
                depth -= 1
        if start < 0:
            continue

        # Walk forward to find the matching closing }
        depth = 0
        end = -1
        for i in range(start, min(len(unescaped), start + 5000)):
            c = unescaped[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            continue

        try:
            trade = json.loads(unescaped[start:end])
            tx_id = trade.get("_txId")
            if tx_id and tx_id not in seen:
                seen.add(tx_id)
                trades.append(trade)
        except json.JSONDecodeError:
            pass

    return trades


def _get_total_pages(html: str) -> int:
    unescaped = _unescape_rsc(html)
    m = re.search(r'"totalPages"\s*:\s*(\d+)', unescaped)
    return int(m.group(1)) if m else 1


def _parse_trade(raw: dict) -> dict | None:
    """Flatten a raw trade dict to the canonical format used by analyzer.py."""
    try:
        issuer = raw.get("issuer") or {}
        ticker_raw = issuer.get("issuerTicker") or ""
        ticker = ticker_raw.split(":")[0].upper().strip()   # "AAPL:US" → "AAPL"

        if not ticker:
            return None

        # Bonds and non-equity instruments have null sector — skip them
        if not issuer.get("sector"):
            return None

        pol = raw.get("politician") or {}

        return {
            "tx_id":    raw.get("_txId"),
            "pub_date": raw.get("pubDate"),
            "tx_date":  raw.get("txDate"),
            "tx_type":  (raw.get("txType") or "").lower(),
            "value":    float(raw.get("value") or 0),
            "ticker":   ticker,
            "sector":   issuer.get("sector") or "unknown",
            "pol_id":   raw.get("_politicianId") or "",
            "pol_name": f"{pol.get('firstName', '')} {pol.get('lastName', '')}".strip(),
        }
    except Exception:
        return None


def _load_cache() -> tuple[list[dict], datetime | None]:
    """Load cached trades. Returns (trades, newest_pub_date)."""
    if not CACHE_FILE.exists():
        return [], None
    try:
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
        return data["trades"], data["newest_pub"]
    except Exception:
        return [], None


def _save_cache(trades: list[dict]) -> None:
    pub_dates = [
        datetime.fromisoformat(t["pub_date"].replace("Z", "+00:00"))
        for t in trades if t.get("pub_date")
    ]
    newest = max(pub_dates) if pub_dates else None
    with open(CACHE_FILE, "wb") as f:
        pickle.dump({"trades": trades, "newest_pub": newest}, f)


def _merge(new: list[dict], cached: list[dict], cutoff: datetime) -> list[dict]:
    seen: set = set()
    result: list[dict] = []
    for t in new + cached:
        if t["tx_id"] in seen:
            continue
        seen.add(t["tx_id"])
        pub = t.get("pub_date", "")
        if pub:
            try:
                if datetime.fromisoformat(pub.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        result.append(t)
    return result


def get_stock_trades(days_back: int = 365, full_fetch: bool = False) -> list[dict]:
    """
    Fetch and parse stock trades from the last `days_back` days.

    full_fetch=False (default, daily use):
        Stops as soon as it catches up to the newest cached trade — fast.
    full_fetch=True (backfill via fetch_all.py):
        Ignores cache boundary and fetches all pages — slow but resumable.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    cached_trades, newest_cached = _load_cache()

    if cached_trades:
        print(f"[scraper] Cache: {len(cached_trades)} trades (newest={newest_cached:%Y-%m-%d})")
    else:
        print("[scraper] No cache — run fetch_all.py first for full history.")

    try:
        html1 = _fetch_page_html(1, days_back)
    except Exception as e:
        if cached_trades and ("429" in str(e) or "Too Many Requests" in str(e)):
            print(f"[scraper] Rate-limited by Capitol Trades — using cached data only.")
            return _merge([], cached_trades, cutoff)
        raise

    total_pages = _get_total_pages(html1)
    print(f"[scraper] {total_pages} pages available.")

    new_trades: list[dict] = []
    seen: set = {t["tx_id"] for t in cached_trades}

    for page in range(1, total_pages + 1):
        html = html1 if page == 1 else _fetch_page_html(page, days_back)
        raw_trades = _extract_trades_from_html(html)

        if not raw_trades:
            break

        stop = False
        for raw in raw_trades:
            pub = raw.get("pubDate", "")
            if pub:
                try:
                    pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if pub_dt < cutoff:
                        stop = True
                        break
                    # Daily mode: stop once we reach already-cached trades
                    if not full_fetch and newest_cached and pub_dt <= newest_cached:
                        stop = True
                        break
                except ValueError:
                    pass
            parsed = _parse_trade(raw)
            if parsed and parsed["tx_id"] not in seen:
                seen.add(parsed["tx_id"])
                new_trades.append(parsed)

        if stop:
            print(f"[scraper] Stopped at page {page}. {len(new_trades)} new trades.")
            break

        if page % 20 == 0:
            # Save partial progress every 20 pages — safe to Ctrl+C and resume
            partial = _merge(new_trades, cached_trades, cutoff)
            _save_cache(partial)
            print(f"[scraper] Page {page}/{total_pages} — {len(partial)} trades cached.")

        if page < total_pages:
            time.sleep(0.8)

    merged = _merge(new_trades, cached_trades, cutoff)
    _save_cache(merged)
    return merged
