"""
Prueba offline del ciclo de ls_bot.py con un cliente Alpaca falso: no toca ninguna cuenta, no usa la red y no escribe en
los ficheros reales. Cubre: entradas en cola para la apertura, simbolo BRK-B -> BRK.B, corto no prestable, no duplicar,
orden de entrada no ejecutada, desvio frente a la apertura oficial, salida con orden al cierre a los 20 dias habiles,
orden al cierre fallida -> cierre a la apertura, conciliacion de precios, signo del retorno en cortos y aviso de noches
sin ejecucion.

Uso:  python ls_test.py
"""
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

for _k in ("ALPACA_KEY", "ALPACA_SECRET", "RESEND_API_KEY", "EMAIL_TO"):     # config.py los exige al importar
    os.environ.setdefault(_k, "offline-test")

import ls_bot  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


class FakeClient:
    def __init__(self):
        self.orders, self.closed, self.positions = {}, [], {}

    def get_account(self):
        return NS(equity="100000", account_number="LS-FAKE")

    def get_all_positions(self):
        return list(self.positions.values())

    def submit_order(self, req):
        o = NS(id=f"o{len(self.orders) + 1}", symbol=req.symbol, qty=req.qty, tif=str(req.time_in_force).upper(),
               sign=1 if "BUY" in str(req.side).upper() else -1, status="accepted", filled_at=None, filled_avg_price=None)
        self.orders[o.id] = o
        return o

    def get_order_by_id(self, oid):
        return self.orders[oid]

    def fill(self, o, price, when):
        """Simula la ejecucion de una orden en cola."""
        o.status, o.filled_at, o.filled_avg_price = "filled", when, str(price)
        if o.symbol in self.positions:                    # orden de salida
            self.positions.pop(o.symbol)
        else:                                             # orden de entrada
            self.positions[o.symbol] = NS(symbol=o.symbol, qty=str(o.sign * o.qty), avg_entry_price=str(price),
                                          current_price=str(price), market_value=str(o.sign * o.qty * price))

    def close_position(self, sym):
        self.closed.append(sym)
        self.positions.pop(sym, None)

    def get_orders(self, req):
        return [NS(filled_at=datetime(2026, 12, 10, 14, 30, tzinfo=timezone.utc), filled_avg_price="108.0")]


class FakeTrading:
    def get_asset(self, sym):
        return NS(shortable=sym != "HARD", easy_to_borrow=sym != "HARD")


def weekdays(a: date, b: date) -> list[date]:
    return [a + timedelta(days=k) for k in range((b - a).days + 1) if (a + timedelta(days=k)).weekday() < 5]


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
    ls_bot.trading_calendar = weekdays
    ls_bot.official_price = lambda t, d, field: 99.5 if field == "Open" else 111.0

    def last_price(sym):
        if sym == "NOPE":
            raise RuntimeError("asset not found for NOPE")
        return 100.0

    ls_bot._last_price = last_price
    cands = [cand("AAA", 1, 5.0), cand("BRK-B", 1, 3.0), cand("DEAD", 1, 2.0), cand("SHRT", -1, 4.0), cand("HARD", -1, 2.0),
             cand("NOPE", 1, 1.0)]
    ls_bot.find_candidates = lambda asof, tk: cands
    state = lambda: json.loads(ls_bot.STATE.read_text())
    missed = lambda: list(csv.DictReader(open(ls_bot.MISSED, encoding="utf-8")))
    d1 = date(2026, 11, 10)

    # noche previa a la sesion d1: las entradas quedan en cola para la apertura
    ls_bot.run(d1)
    st = state()
    assert set(st["positions"]) == {"AAA", "BRK-B", "DEAD", "SHRT"}, st["positions"]
    assert {o.symbol for o in fake.orders.values()} == {"AAA", "BRK.B", "DEAD", "SHRT"}                # simbolo Alpaca
    assert all(o.qty == 50 and "DAY" in o.tif for o in fake.orders.values())                           # 5 % de 100.000 a 100 $
    assert {o.symbol: o.sign for o in fake.orders.values()}["SHRT"] == -1
    assert all(p["entry_date"] == "2026-11-10" and p["exit_date"] == "2026-12-08" for p in st["positions"].values())
    assert {m["ticker"] for m in missed()} == {"HARD", "NOPE"}, missed()
    assert [r["date"] for r in csv.DictReader(open(ls_bot.EQUITY, encoding="utf-8"))] == ["2026-11-09"]  # cierre anterior
    print("OK noche 1: 4 ordenes en cola, simbolo BRK.B, corto no prestable y activo inexistente registrados; salida 2026-12-08")

    # misma noche otra vez: no duplica
    ls_bot.run(d1)
    assert len(fake.orders) == 4, "duplico ordenes"
    print("OK repeticion: no duplica entradas ni el registro del dia")

    # apertura de d1: tres ordenes se ejecutan a 100; la de DEAD se cancela
    opened = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
    for o in list(fake.orders.values()):
        if o.symbol == "DEAD":
            o.status = "canceled"
        else:
            fake.fill(o, 100.0, opened)
    ls_bot.find_candidates = lambda asof, tk: []

    # noche siguiente: comprueba las entradas
    ls_bot.run(date(2026, 11, 11))
    st = state()
    assert set(st["positions"]) == {"AAA", "BRK-B", "SHRT"}, st["positions"]
    assert any(m["ticker"] == "DEAD" and "no ejecutada" in m["reason"] for m in missed())
    assert abs(st["positions"]["AAA"]["entry_slip_bp"] - 50.3) < 0.1                                    # compro 0,5 % sobre la apertura
    assert abs(st["positions"]["SHRT"]["entry_slip_bp"] + 50.3) < 0.1                                   # vendio en corto mas caro: a favor
    print("OK noche 2: ejecuciones comprobadas, orden cancelada retirada, desvio frente a la apertura medido")

    # noche previa a la sesion de salida (se han saltado noches: debe avisar)
    exit_d = date(2026, 12, 8)
    ls_bot.run(exit_d)
    st = state()
    cls = [o for o in fake.orders.values() if "CLS" in o.tif]
    assert {o.symbol: o.sign for o in cls} == {"AAA": -1, "BRK.B": -1, "SHRT": 1}, cls                 # vender largos, recomprar el corto
    assert all(p.get("closing") for p in st["positions"].values()) and not ls_bot.CLOSED.exists()
    assert any(m["ticker"] == "*" and "no corrio" in m["reason"] for m in missed())
    print("OK noche de salida: 3 ordenes al cierre enviadas, posiciones aun abiertas, aviso de noches sin ejecucion")

    # cierre del dia de salida: AAA y SHRT se ejecutan a 110; la orden de BRK.B expira
    closed_at = datetime(2026, 12, 8, 21, 0, tzinfo=timezone.utc)
    for o in cls:
        if o.symbol == "BRK.B":
            o.status = "expired"
        else:
            fake.fill(o, 110.0, closed_at)

    # noche siguiente: registra los cierres y cierra BRK.B a la apertura
    ls_bot.run(date(2026, 12, 9))
    assert state()["positions"] == {} and fake.closed == ["BRK.B"], (state()["positions"], fake.closed)
    rows = {r["ticker"]: r for r in csv.DictReader(open(ls_bot.CLOSED, encoding="utf-8"))}
    a, s, b = rows["AAA"], rows["SHRT"], rows["BRK-B"]
    assert a["exit_type"] == "cierre" and a["exit_price_source"] == "fill" and a["exit_price"] == "110.00"
    assert a["exit_date"] == "2026-12-08" and a["days_held"] == "28"                                    # 20 dias habiles
    assert abs(float(a["return_pct"]) - 0.10) < 1e-6                                                    # largo: +10 %
    assert abs(float(s["return_pct"]) + 0.10) < 1e-6                                                    # corto: precio sube -> -10 %
    assert abs(float(a["exit_slip_bp"]) - 90.1) < 0.1 and abs(float(s["exit_slip_bp"]) + 90.1) < 0.1    # frente al cierre oficial 111
    assert abs(float(a["entry_slip_bp"]) - 50.3) < 0.1
    assert b["exit_type"] == "apertura" and b["exit_price_source"] == "fill" and b["exit_price"] == "108.00"
    assert b["planned_exit"] == "2026-12-08" and b["exit_date"] == "2026-12-10"
    print("OK cierres: precios reales, signo del corto, desvio frente al cierre oficial, orden fallida cerrada a la apertura")

    # con el mercado abierto (lanzado a mano) las ordenes son inmediatas y el dia registrado es hoy
    ls_bot.find_candidates = lambda asof, tk: [dict(cand("ZZZ", 1, 9.0), event_date="2026-12-09", entry_day="2026-12-10")]
    ls_bot.run(date(2026, 12, 10), market_open=True)
    assert "ZZZ" in state()["positions"]
    assert [r["date"] for r in csv.DictReader(open(ls_bot.EQUITY, encoding="utf-8"))][-1] == "2026-12-10"
    print("OK mercado abierto: entrada inmediata y registro con fecha de hoy")
    print("TODO OK")


if __name__ == "__main__":
    main()
