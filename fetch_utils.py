"""
Shared helper to download data for many tickers without tripping yfinance's rate limit.

yfinance throttles by request volume over a window longer than a few minutes, so one burst of ~500 requests
leaves the tail of the list without data. Two defences, both here:
  1. the order is shuffled on every run, so the unlucky tail is not always the same companies
  2. the work is split into batches with a long pause between them, so the quota can recover
"""
import random
import time
from concurrent.futures import ThreadPoolExecutor


def run_in_batches(items: list, fn, workers: int = 3, batches: int = 2, pause: float = 90.0,
                   sleep=time.sleep, rng: random.Random | None = None) -> list[tuple]:
    """Apply fn to every item, in shuffled order and in `batches` groups separated by `pause` seconds.
    Returns [(item, fn(item)), ...] in the order they were processed."""
    order = list(items)
    (rng or random).shuffle(order)
    if not order:
        return []
    size = -(-len(order) // max(1, batches))                      # ceiling division
    out: list[tuple] = []
    for i in range(0, len(order), size):
        if i:
            print(f"[fetch] pausing {pause:.0f}s between batches to let the rate limit recover")
            sleep(pause)
        chunk = order[i:i + size]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            out += list(zip(chunk, pool.map(fn, chunk)))
    return out
