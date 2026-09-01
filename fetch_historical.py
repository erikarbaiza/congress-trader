"""
Descarga histórica completa de Capitol Trades (hasta 5 años).
Acumula en trades_cache.pkl sin borrar lo que ya hay.

Uso: python fetch_historical.py
  - Delay de 2 s entre páginas (respetuoso con Capitol Trades)
  - Guarda progreso cada 20 páginas (seguro interrumpir y relanzar)
  - Al terminar imprime auditoría completa

Después de esto, ejecuta backtest.py.
"""

import pickle
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from scraper import get_stock_trades

PAGE_DELAY = 2.0   # segundos entre páginas (no saturar Capitol Trades)
CACHE_FILE = Path(__file__).parent / "trades_cache.pkl"


def _audit(trades: list[dict]) -> None:
    if not trades:
        print("[audit] Cache vacio.")
        return

    pub_dates = sorted(t["pub_date"][:10] for t in trades if t.get("pub_date"))
    tx_dates  = sorted(t["tx_date"][:10]  for t in trades if t.get("tx_date"))

    buys  = sum(1 for t in trades if t["tx_type"] == "buy")
    sells = sum(1 for t in trades if t["tx_type"] == "sell")

    oldest  = pub_dates[0]
    newest  = pub_dates[-1]
    days    = (datetime.fromisoformat(newest) - datetime.fromisoformat(oldest)).days
    years   = days / 365

    # Periodos walk-forward disponibles (ventana ranking 365d, paso 30d)
    usable_start = datetime.fromisoformat(oldest).replace(month=1, day=1)
    usable_start = datetime.fromisoformat(oldest)
    from datetime import timedelta
    bt_start = datetime.fromisoformat(oldest) + timedelta(days=365)
    bt_end   = datetime.fromisoformat(newest)
    wf_periods = max(0, (bt_end - bt_start).days // 30)

    buys_by_pol: dict[str, int] = defaultdict(int)
    for t in trades:
        if t["tx_type"] == "buy":
            buys_by_pol[t["pol_id"]] += 1

    print()
    print("=" * 60)
    print("AUDITORÍA DE DATOS — RESULTADO FINAL")
    print("=" * 60)
    print(f"  Total trades en cache:   {len(trades):>7,}")
    print(f"    Buys:                  {buys:>7,}")
    print(f"    Sells:                 {sells:>7,}")
    print()
    print(f"  pub_date más antigua:    {oldest}")
    print(f"  pub_date más reciente:   {newest}")
    print(f"  Rango total:             {days} días ({years:.1f} años)")
    print()
    print(f"  tx_date más antigua:     {tx_dates[0]}")
    print(f"  tx_date más reciente:    {tx_dates[-1]}")
    print()
    print(f"  Políticos distintos:     {len(set(t['pol_id'] for t in trades)):>4}")
    print(f"  Tickers distintos:       {len(set(t['ticker'] for t in trades)):>4}")
    print()
    print(f"  Políticos con >= 5 buys: {sum(1 for n in buys_by_pol.values() if n >= 5):>4}")
    print(f"  Políticos con >=10 buys: {sum(1 for n in buys_by_pol.values() if n >= 10):>4}")
    print()
    print(f"  Periodos walk-forward disponibles (~30d cada uno): {wf_periods}")
    if wf_periods >= 24:
        print("  → SUFICIENTE para un backtest robusto (>=24 periodos)")
    elif wf_periods >= 12:
        print("  → ACEPTABLE para un primer backtest (>=12 periodos)")
    else:
        print("  → INSUFICIENTE — necesitas más histórico")
    print()
    print("  Distribución por año (pub_date):")
    by_year = Counter(t["pub_date"][:4] for t in trades if t.get("pub_date"))
    for yr, cnt in sorted(by_year.items()):
        bar = "#" * (cnt // 30)
        print(f"    {yr}: {cnt:>5} trades  {bar}")
    print("=" * 60)


if __name__ == "__main__":
    print("[fetch_historical] Descargando TODO el histórico de Capitol Trades (txDate=all)")
    print(f"[fetch_historical] Delay entre páginas: {PAGE_DELAY}s (modo respetuoso)")
    print("[fetch_historical] Capitol Trades tiene ~3081 páginas — tiempo estimado ~100 min")
    print("[fetch_historical] Progreso guardado cada 20 páginas — seguro interrumpir con Ctrl+C")
    print()

    trades = get_stock_trades(
        days_back=99999,   # sin cutoff efectivo
        full_fetch=True,
        page_delay=PAGE_DELAY,
        tx_date="all",
    )

    _audit(trades)
