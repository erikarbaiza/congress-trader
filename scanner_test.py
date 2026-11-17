"""
Offline test of the scanner's retry logic (no network, no waiting): a throttle that clears only after some time must be
recovered by the cool-down rounds, a block that never clears must be reported and must respect the time budget.

Usage:  python scanner_test.py
"""
import os
import sys

for _k in ("ALPACA_KEY", "ALPACA_SECRET", "RESEND_API_KEY", "EMAIL_TO"):     # config.py requires them on import
    os.environ.setdefault(_k, "offline-test")

import earnings_scanner as sc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


class Clock:
    """Fake time: sleep() advances it, so the test runs instantly."""
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


def main() -> None:
    # 1. throttle that clears after 100 s: tickers fail until then, succeed afterwards
    clk = Clock()
    beat = {"ticker": "WFC", "surprise_pct": 9.0}

    def throttled(sym, days_back, attempts=3, pause=1.5):
        if clk.now() < 100:
            return sc._FAILED
        return beat if sym == "WFC" else None

    beats, still = sc.retry_failed(["WFC", "WM", "XEL"], 3, check=throttled, sleep=clk.sleep, now=clk.now)
    assert beats == [beat] and still == [], (beats, still)
    print(f"OK throttle recovered after the cool-down (round 2 at t={clk.now():.0f}s): beat found, nothing left failing")

    # 2. block that never clears: everything is reported and the time budget is respected
    clk = Clock()
    always = lambda sym, days_back, attempts=3, pause=1.5: sc._FAILED
    beats, still = sc.retry_failed(["A", "B", "C"], 3, check=always, sleep=clk.sleep, now=clk.now)
    assert beats == [] and still == ["A", "B", "C"], (beats, still)
    print(f"OK persistent block: reported ({len(still)} tickers), finished at t={clk.now():.0f}s")

    # 3. budget exhausted mid-round keeps the unprocessed tickers in the failed list
    clk = Clock()
    names = [f"T{i}" for i in range(2000)]
    beats, still = sc.retry_failed(names, 3, check=always, sleep=clk.sleep, now=clk.now, budget=300)
    assert sorted(still) == sorted(names) and clk.now() < 300 + 200, (len(still), clk.now())
    print(f"OK budget: 2000 failing tickers stop at t={clk.now():.0f}s without losing any from the failed list")

    # 4. nothing to retry -> no waiting
    clk = Clock()
    assert sc.retry_failed([], 3, check=always, sleep=clk.sleep, now=clk.now) == ([], []) and clk.now() == 0
    print("OK nothing failed: no waiting")

    # 5. batches: every ticker processed exactly once, shuffled differently per run, one long pause between batches
    import random
    from fetch_utils import run_in_batches
    names = [f"T{i}" for i in range(503)]
    pauses = []
    out1 = run_in_batches(names, lambda s: s.lower(), sleep=pauses.append, rng=random.Random(1))
    out2 = run_in_batches(names, lambda s: s.lower(), sleep=lambda s: None, rng=random.Random(2))
    assert sorted(t for t, _ in out1) == sorted(names) and all(r == t.lower() for t, r in out1)
    assert pauses == [90.0], pauses
    assert [t for t, _ in out1] != names and [t for t, _ in out1] != [t for t, _ in out2]
    print(f"OK batches: 503 tickers once each, shuffled differently per run, {len(pauses)} pause of {pauses[0]:.0f}s between 2 batches")
    assert run_in_batches([], lambda s: s, sleep=pauses.append) == []
    one = run_in_batches(["A"], lambda s: s, sleep=pauses.append)
    assert one == [("A", "A")] and pauses == [90.0]
    print("OK batches: empty list and single ticker need no pause")
    print("TODO OK")


if __name__ == "__main__":
    main()
