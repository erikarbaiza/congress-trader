"""
Prueba offline del ciclo de ls_bot.py con un cliente Alpaca falso (no toca ninguna cuenta ni escribe en los ficheros reales).
Cubre: entradas largas/cortas, simbolo BRK-B -> BRK.B, corto no prestable, no duplicar, salida a 20 dias habiles,
conciliacion de precios de salida y signo del retorno en cortos.

Uso:  python ls_test.py
"""
import csv
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import ls_bot

sys.stdout.reconfigure(encoding="utf-8")


class FakeClient:
    def __init__(self):
        self.orders, self.closed, self.positions = [], [], {}

    def get_account(self):
        return NS(equity="100000", account_number="LS-FAKE")

    def get_all_positions(self):
        return list(self.positions.values())

    def submit_order(self, req):
        sign = 1 if "BUY" in str(req.side).upper() else -1
        self.orders.append((req.symbol, req.qty, sign))
        self.positions[req.symbol] = NS(symbol=req.symbol, avg_entry_price="100", current_price="100",
                                        market_value=str(sign * req.qty * 100.0))

    def close_position(self, sym):
        self.closed.append(sym)
        self.positions.pop(sym, None)

    def get_orders(self, req):
        return [NS(filled_at=datetime(2026, 12, 9, 15, 0, tzinfo=timezone.utc), filled_avg_price="110.0")]


class FakeTrading:
    def get_asset(self, sym):
        if sym == "NOPE":
            raise RuntimeError("asset not found for NOPE")
        return NS(shortable=sym != "HARD", easy_to_borrow=sym != "HARD")


def cand(t, side, strength):
    return {"ticker": t, "side": side, "strength": strength, "surprise_px": side * strength, "surprise_pct": side * 10.0,
            "event_date": "2026-11-09", "reaction_day": "2026-11-09", "entry_day": "2026-11-10"}


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    ls_bot.STATE, ls_bot.CLOSED, ls_bot.EQUITY, ls_bot.MISSED = (tmp / n for n in ("s.json", "c.csv", "e.csv", "m.csv"))
    fake = FakeClient()
    ls_bot._ls_client = lambda: fake
    ls_bot._get_trading_client = lambda: FakeTrading()
    ls_bot.sp500_tickers = lambda: []
    def last_price(sym):
        if sym == "NOPE":
            raise RuntimeError("asset not found for NOPE")
        return 100.0

    ls_bot._last_price = last_price
    cands = [cand("AAA", 1, 5.0), cand("BRK-B", 1, 3.0), cand("SHRT", -1, 4.0), cand("HARD", -1, 2.0), cand("NOPE", 1, 1.0)]
    ls_bot.find_candidates = lambda asof, tk: cands

    # dia 1: entradas
    ls_bot.run(date(2026, 11, 10), dry=False)
    st = json.loads(ls_bot.STATE.read_text())
    assert set(st["positions"]) == {"AAA", "BRK-B", "SHRT"}, st["positions"]
    assert {o[0] for o in fake.orders} == {"AAA", "BRK.B", "SHRT"}, fake.orders                    # simbolo Alpaca
    assert all(o[1] == 50 for o in fake.orders), fake.orders                                        # 5 % de 100.000 a 100 $
    assert {o[0]: o[2] for o in fake.orders}["SHRT"] == -1
    missed = list(csv.DictReader(open(ls_bot.MISSED, encoding="utf-8")))
    assert {m["ticker"] for m in missed} == {"HARD", "NOPE"}, missed
    exit_d = st["positions"]["AAA"]["exit_date"]
    print("OK dia 1: 3 entradas, simbolo BRK.B, corto no prestable y activo inexistente registrados; salida prevista", exit_d)

    # dia 1 otra vez: no duplica
    n = len(fake.orders)
    ls_bot.run(date(2026, 11, 10), dry=False)
    assert len(fake.orders) == n, "duplico ordenes"
    print("OK repeticion: no duplica entradas")

    # dia de salida
    ls_bot.find_candidates = lambda asof, tk: []
    ls_bot.run(date.fromisoformat(exit_d), dry=False)
    assert sorted(fake.closed) == ["AAA", "BRK.B", "SHRT"], fake.closed
    assert json.loads(ls_bot.STATE.read_text())["positions"] == {}
    rows = {r["ticker"]: r for r in csv.DictReader(open(ls_bot.CLOSED, encoding="utf-8"))}
    assert rows["AAA"]["exit_price_source"] == "fill" and rows["AAA"]["exit_price"] == "110.00"
    assert abs(float(rows["AAA"]["return_pct"]) - 0.10) < 1e-6                                       # largo: +10 %
    assert abs(float(rows["SHRT"]["return_pct"]) + 0.10) < 1e-6                                      # corto: precio sube -> -10 %
    days = int(rows["AAA"]["days_held"])
    assert 25 <= days <= 32, days                                                                    # ~20 dias habiles
    print(f"OK salida: 3 cierres, conciliacion con la ejecucion real, corto con signo correcto, {days} dias naturales")
    print("TODO OK")


if __name__ == "__main__":
    main()
