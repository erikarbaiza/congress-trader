"""
One-time full historical fetch. Run this first to build the local cache.
Saves progress every 20 pages — safe to Ctrl+C and re-run.
On subsequent runs (and in main.py), only new pages are downloaded.
"""

from scraper import get_stock_trades

print("Starting full historical fetch (365 days)…")
print("This will take 10-20 minutes on first run. Progress is saved every 20 pages.")
print("Safe to interrupt with Ctrl+C — re-run to continue from last save.")
print()

try:
    trades = get_stock_trades(days_back=365, full_fetch=True)
    print(f"\nDone. {len(trades)} trades in cache.")
    buys = [t for t in trades if t['tx_type'] == 'buy']
    print(f"Buys: {len(buys)}, Sells: {len(trades)-len(buys)}")
    print(f"Politicians covered: {len(set(t['pol_id'] for t in trades))}")
except KeyboardInterrupt:
    print("\nInterrupted — partial data saved in trades_cache.pkl. Re-run to continue.")
