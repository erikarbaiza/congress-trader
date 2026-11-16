"""
Modulo paper trading largo/corto: hipotesis preregistrada, especificacion congelada (ver ls_config.py).

Corre una vez por noche CON EL MERCADO CERRADO (workflow run_ls.yml), en una cuenta Alpaca paper APARTE
(ALPACA_LS_KEY / ALPACA_LS_SECRET). Todo se refiere a la "sesion objetivo": la proxima apertura del mercado.
  1. comprueba las ordenes enviadas en noches anteriores: ejecucion real de cada entrada y su desvio frente a la apertura
     oficial; salidas al cierre ya ejecutadas; ordenes que no llegaron a ejecutarse
  2. salidas: la posicion cuyo dia de salida es la sesion objetivo recibe una orden al cierre (CLS), como en el backtest
     (cierre del dia habil 20). Si esa orden falla, la noche siguiente se cierra a la apertura
  3. entradas: largos / cortos (5 % del patrimonio cada uno, max 10 por lado) para los anuncios cuyo dia de entrada es la
     sesion objetivo (o la anterior, si el bot no corrio). La orden queda en cola y se ejecuta en la apertura, como en el
     backtest. Los cortos, solo si Alpaca los marca shortable y easy_to_borrow
  4. registra el dia en ls_equity.csv; todo lo no ejecutado queda en ls_missed.csv con su motivo

Si se lanza a mano con el mercado abierto, la sesion objetivo es hoy y las ordenes se ejecutan en el momento.

Archivos de estado: ls_state.json, ls_closed.csv, ls_equity.csv, ls_missed.csv.
Seguridad: se niega a operar si la cuenta es la misma que la del bot PEAD (su stop-loss trata los cortos como largos).

Uso:  python ls_bot.py [--dry-run] [--asof AAAA-MM-DD]     (--asof = sesion objetivo; solo con --dry-run)
"""
import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from ls_config import LS_HOLD_DAYS, LS_KEY, LS_MAX_PER_SIDE, LS_POSITION_PCT, LS_SECRET
from ls_signals import find_candidates, official_price, sp500_tickers, trading_calendar
from trader import _get_data_client, _get_trading_client

ROOT = Path(__file__).parent
STATE, CLOSED, EQUITY, MISSED = (ROOT / n for n in ("ls_state.json", "ls_closed.csv", "ls_equity.csv", "ls_missed.csv"))
CLOSED_COLS = ["ticker", "side", "event_date", "entry_date", "exit_date", "days_held", "entry_price", "exit_price",
               "return_pct", "surprise_px", "surprise_pct", "exit_price_source",
               # calidad de ejecucion: desvio en puntos basicos frente al precio teorico del backtest (positivo = coste)
               "planned_exit", "exit_type", "entry_open", "entry_slip_bp", "exit_close", "exit_slip_bp"]
EQUITY_COLS = ["date", "equity", "spy_price", "n_long", "n_short", "long_mv", "short_mv"]
MISSED_COLS = ["date", "ticker", "side", "reason", "surprise_px", "event_date"]
DEAD = {"canceled", "expired", "rejected"}     # estados finales de una orden que no se ejecuto


# ── utilidades ───────────────────────────────────────────────────────────────
def _ls_client() -> TradingClient | None:
    if not (LS_KEY and LS_SECRET):
        return None
    client = TradingClient(LS_KEY, LS_SECRET, paper=True)     # siempre paper
    if client.get_account().account_number == _get_trading_client().get_account().account_number:
        raise SystemExit("[ls] ABORTADO: ALPACA_LS_KEY apunta a la misma cuenta que el bot PEAD. Crea una cuenta paper aparte.")
    return client


def _target_session(client: TradingClient | None) -> tuple[date, bool]:
    """(sesion en la que se ejecutaran las ordenes enviadas ahora, mercado abierto). Fechas en hora de Nueva York."""
    c = (client or _get_trading_client()).get_clock()
    return (c.timestamp.date(), True) if c.is_open else (c.next_open.date(), False)


def _alp(ticker: str) -> str:
    """yfinance escribe BRK-B; Alpaca escribe BRK.B."""
    return ticker.replace("-", ".")


def _last_price(symbol: str) -> float:
    r = _get_data_client().get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))
    return float(r[symbol].price)


def _status(order) -> str:
    return str(getattr(order.status, "value", order.status)).lower()


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"positions": {}, "seen": []}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append(path: Path, cols: list[str], row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in cols})


def _missed(day: date, c: dict, reason: str, dry: bool) -> None:
    print(f"  [ls] NO EJECUTADO {c['ticker']:6} {({1: 'LARGO', -1: 'CORTO'}).get(c['side'], 'sin lado')} — {reason}")
    if not dry:
        _append(MISSED, MISSED_COLS, {"date": day.isoformat(), "ticker": c["ticker"], "side": c["side"], "reason": reason,
                                      "surprise_px": c.get("surprise_px"), "event_date": c["event_date"]})


def _closed_row(t: str, p: dict, entry_px: float, exit_px: float, exit_day: date, source: str, exit_type: str) -> dict:
    return {"ticker": t, "side": p["side"], "event_date": p["event_date"], "entry_date": p["entry_date"],
            "exit_date": exit_day.isoformat(), "days_held": (exit_day - date.fromisoformat(p["entry_date"])).days,
            "entry_price": f"{entry_px:.2f}", "exit_price": f"{exit_px:.2f}",
            "return_pct": f"{(exit_px / entry_px - 1) * p['side']:.4f}", "surprise_px": p["surprise_px"],
            "surprise_pct": p["surprise_pct"], "exit_price_source": source, "planned_exit": p["exit_date"],
            "exit_type": exit_type, "entry_open": p.get("entry_open", ""), "entry_slip_bp": p.get("entry_slip_bp", "")}


def _add_exit_slip(row: dict, done: date) -> bool:
    """Desvio de la salida frente al cierre oficial del dia previsto (el precio que usa el backtest). True si lo anade."""
    if row.get("exit_close") or row.get("exit_price_source") != "fill":
        return False
    planned = date.fromisoformat(row.get("planned_exit") or row["exit_date"])
    close = official_price(row["ticker"], planned, "Close") if planned <= done else None
    if not close:
        return False
    row["exit_close"] = f"{close:.2f}"
    row["exit_slip_bp"] = f"{-int(row['side']) * (float(row['exit_price']) / close - 1) * 1e4:.1f}"
    return True


# ── comprobacion de ordenes enviadas en ejecuciones anteriores ───────────────
def check_entries(client: TradingClient, state: dict, session: date, done: date) -> None:
    """Guarda la ejecucion real de cada entrada y su desvio frente a la apertura oficial; retira las que no se ejecutaron."""
    for t, p in list(state["positions"].items()):
        if p.get("order_id") and not p.get("entry_fill"):
            try:
                o = client.get_order_by_id(p["order_id"])
            except Exception as e:
                print(f"  [ls] AVISO: no se pudo consultar la orden de entrada de {t} ({str(e)[:80]})")
                continue
            if o.filled_at and o.filled_avg_price:
                p["entry_fill"] = round(float(o.filled_avg_price), 4)
            elif _status(o) in DEAD:
                _missed(session, {"ticker": t, **p}, f"orden de entrada no ejecutada ({_status(o)})", dry=False)
                state["positions"].pop(t)
                continue
        if p.get("entry_fill") and "entry_slip_bp" not in p and date.fromisoformat(p["entry_date"]) <= done:
            opn = official_price(t, date.fromisoformat(p["entry_date"]), "Open")
            if opn:
                p["entry_open"] = round(opn, 4)
                p["entry_slip_bp"] = round(p["side"] * (p["entry_fill"] / opn - 1) * 1e4, 1)
                print(f"  [ls] {t:6} entrada ejecutada a {p['entry_fill']:.2f} (apertura oficial {opn:.2f}, "
                      f"desvio {p['entry_slip_bp']:+.1f} pb)")


def check_closings(client: TradingClient, state: dict, done: date) -> None:
    """Salidas al cierre enviadas en una ejecucion anterior: si se ejecutaron, pasan al diario con su precio real."""
    for t, p in list(state["positions"].items()):
        if not p.get("closing"):
            continue
        try:
            o = client.get_order_by_id(p["closing"])
        except Exception as e:
            print(f"  [ls] AVISO: no se pudo consultar la orden de salida de {t} ({str(e)[:80]})")
            continue
        if o.filled_at and o.filled_avg_price:
            row = _closed_row(t, p, p["entry_px"], float(o.filled_avg_price), o.filled_at.date(), "fill", "cierre")
            _add_exit_slip(row, done)
            _append(CLOSED, CLOSED_COLS, row)
            state["positions"].pop(t)
            print(f"  [ls] {t:6} cerrada al cierre a {row['exit_price']} ({float(row['return_pct']):+.2%})")
        elif _status(o) in DEAD:
            print(f"  [ls] AVISO: la orden al cierre de {t} no se ejecuto ({_status(o)}); se cierra a la apertura")
            p.pop("closing")


def reconcile(client: TradingClient, done: date | None = None) -> int:
    """Sustituye el precio de salida estimado por la ejecucion real (cubrir un corto = compra; cerrar un largo = venta)
    y completa el desvio frente al cierre oficial cuando ya se conoce."""
    rows = _rows(CLOSED)
    n = changed = 0
    for r in rows:
        if r["exit_price_source"] != "fill":
            side = OrderSide.SELL if r["side"] == "1" else OrderSide.BUY
            # la orden pudo enviarse la tarde anterior (hora de Nueva York), de ahi el dia de margen
            since = datetime.fromisoformat(r["exit_date"]).replace(tzinfo=timezone.utc) - timedelta(days=1)
            orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[_alp(r["ticker"])],
                                                        side=side, after=since, limit=50))
            fills = [o for o in orders if o.filled_at and o.filled_avg_price]
            if fills:
                o = min(fills, key=lambda x: x.filled_at)
                entry, px = float(r["entry_price"]), float(o.filled_avg_price)
                r["exit_price"], r["exit_price_source"] = f"{px:.2f}", "fill"
                r["return_pct"] = f"{(px / entry - 1) * int(r['side']):.4f}"
                r["exit_date"] = o.filled_at.strftime("%Y-%m-%d")
                n += 1
        if done and _add_exit_slip(r, done):
            changed += 1
    if n or changed:
        with open(CLOSED, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CLOSED_COLS)
            w.writeheader()
            w.writerows({c: r.get(c, "") for c in CLOSED_COLS} for r in rows)
    return n


# ── ciclo ────────────────────────────────────────────────────────────────────
def run(session: date | None = None, dry: bool = False, market_open: bool = False) -> None:
    client = _ls_client()
    if client is None and not dry:
        print("[ls] ALPACA_LS_KEY / ALPACA_LS_SECRET no configuradas: modulo largo/corto desactivado.")
        return
    if session is None:
        session, market_open = _target_session(client)
    cal = trading_calendar(session - timedelta(days=40), session + timedelta(days=60))
    if session not in cal:
        print(f"[ls] {session} no es dia de mercado.")
        return
    i = cal.index(session)
    done = cal[i - 1]                       # ultima sesion completa (sus precios oficiales ya existen)
    exit_date = cal[i + LS_HOLD_DAYS]       # entrada = dia 0; salida al cierre del dia habil 20, como en el backtest
    print(f"[ls] sesion objetivo {session} ({'mercado abierto: ordenes inmediatas' if market_open else 'mercado cerrado: ordenes en cola'})")
    equity = float(client.get_account().equity) if client else 100_000.0
    state = _load_state()
    if client and not dry:
        check_entries(client, state, session, done)
        check_closings(client, state, done)
        n = reconcile(client, done)
        if n:
            print(f"[ls] diario: {n} precio(s) de salida sustituido(s) por la ejecucion real")
    live = {p.symbol: p for p in client.get_all_positions()} if client else {}
    orphans = sorted(set(live) - {_alp(t) for t in state["positions"]})
    if orphans:
        print(f"::warning::[ls] posiciones en la cuenta que no estan en el estado (revisar a mano): {orphans}")

    # 1. salidas
    for t, p in list(state["positions"].items()):
        planned = date.fromisoformat(p["exit_date"])
        if planned > session or p.get("closing"):
            continue
        lp = live.get(_alp(t))
        if lp is None:
            print(f"  [ls] {t}: sin posicion en la cuenta (orden de entrada no ejecutada); se retira del estado")
            state["positions"].pop(t)
            continue
        entry_px, last, side = float(lp.avg_entry_price), float(lp.current_price), p["side"]
        at_close = planned == session and not market_open
        how = f"orden al cierre del {session}" if at_close else ("ahora" if market_open else "a la apertura (fuera de plazo)")
        print(f"  [ls] SALIDA {t:6} {'LARGO' if side == 1 else 'CORTO'} — {LS_HOLD_DAYS} dias habiles; {how}")
        if dry:
            continue
        try:
            if at_close:
                o = client.submit_order(MarketOrderRequest(symbol=_alp(t), qty=abs(int(float(lp.qty))),
                                                           side=OrderSide.SELL if side == 1 else OrderSide.BUY,
                                                           time_in_force=TimeInForce.CLS))
                p.update(closing=str(o.id), entry_px=entry_px)
            else:
                client.close_position(_alp(t))
                _append(CLOSED, CLOSED_COLS, _closed_row(t, p, entry_px, last, session, "estimate",
                                                         "intradia" if market_open else "apertura"))
                state["positions"].pop(t)
        except Exception as e:
            print(f"  [ls] AVISO: no se pudo enviar la salida de {t} ({str(e)[:100]}); se reintenta en la proxima ejecucion")

    # 2. entradas
    cands = find_candidates(session, sp500_tickers())
    for c in [c for c in cands if c["side"] == 0]:
        _missed(session, c, c.get("reason", "no resuelto"), dry)
    n_side = {1: sum(1 for p in state["positions"].values() if p["side"] == 1),
              -1: sum(1 for p in state["positions"].values() if p["side"] == -1)}
    for side in (1, -1):
        for c in sorted((c for c in cands if c["side"] == side), key=lambda x: -x["strength"]):
            t, key = c["ticker"], f"{c['ticker']}|{c['event_date']}"
            if key in state["seen"] or t in state["positions"]:
                continue
            if n_side[side] >= LS_MAX_PER_SIDE:
                _missed(session, c, f"tope de {LS_MAX_PER_SIDE} posiciones por lado", dry)
                state["seen"].append(key)
                continue
            try:
                if side == -1:
                    a = _get_trading_client().get_asset(_alp(t))
                    if not (a.shortable and a.easy_to_borrow):
                        _missed(session, c, "no se puede vender en corto (shortable/easy_to_borrow)", dry)
                        state["seen"].append(key)
                        continue
                price = _last_price(_alp(t))
                qty = math.floor(equity * LS_POSITION_PCT / price)
                if qty < 1:
                    _missed(session, c, "cantidad < 1 accion", dry)
                    state["seen"].append(key)
                    continue
                late = "" if c.get("entry_day", session.isoformat()) == session.isoformat() else " [un dia tarde]"
                print(f"  [ls] ENTRADA {t:6} {'LARGO' if side == 1 else 'CORTO'} x{qty} (~{qty * price:,.0f} $) — sorpresa/precio "
                      f"{c['surprise_px']:+.3f}%, sorpresa {c['surprise_pct']:+.1f}%{late}")
                if not dry:
                    o = client.submit_order(MarketOrderRequest(symbol=_alp(t), qty=qty,
                                                               side=OrderSide.BUY if side == 1 else OrderSide.SELL,
                                                               time_in_force=TimeInForce.DAY))
                    state["positions"][t] = {"side": side, "event_date": c["event_date"], "entry_date": session.isoformat(),
                                             "exit_date": exit_date.isoformat(), "surprise_px": c["surprise_px"],
                                             "surprise_pct": c["surprise_pct"], "qty": qty, "order_id": str(o.id),
                                             "planned_entry": c.get("entry_day", session.isoformat())}
                    state["seen"].append(key)
            except Exception as e:
                # un fallo en un valor no para el ciclo; si es "activo no encontrado" no se reintenta, si no, si
                _missed(session, c, f"error: {str(e)[:80]}", dry)
                if "not found" in str(e).lower():
                    state["seen"].append(key)
                continue
            n_side[side] += 1

    # 3. registro del dia: con el mercado cerrado, el patrimonio corresponde al cierre de la ultima sesion completa
    if client and not dry:
        log_day = session if market_open else done
        logged = [r["date"] for r in _rows(EQUITY)]
        if logged:
            gap = [d for d in cal if date.fromisoformat(logged[-1]) < d < log_day]
            if gap:
                msg = f"el bot no corrio en {len(gap)} sesion(es): {gap[0]} a {gap[-1]}"
                print(f"::warning::[ls] {msg}")
                _append(MISSED, MISSED_COLS, {"date": log_day.isoformat(), "ticker": "*", "reason": msg})
        if log_day.isoformat() not in logged:
            mv = [float(p.market_value) for p in client.get_all_positions()]
            _append(EQUITY, EQUITY_COLS, {"date": log_day.isoformat(), "equity": f"{float(client.get_account().equity):.2f}",
                                          "spy_price": f"{_last_price('SPY'):.2f}", "n_long": sum(1 for x in mv if x > 0),
                                          "n_short": sum(1 for x in mv if x < 0), "long_mv": f"{sum(x for x in mv if x > 0):.2f}",
                                          "short_mv": f"{sum(x for x in mv if x < 0):.2f}"})
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        reconcile(client, done)
    slips = [float(x) for x in ([p.get("entry_slip_bp", "") for p in state["positions"].values()]
                                + [r.get("entry_slip_bp", "") for r in _rows(CLOSED)]) if x not in ("", None)]
    if slips:
        print(f"[ls] desvio medio de entrada frente a la apertura oficial: {sum(slips) / len(slips):+.1f} pb en {len(slips)} "
              f"entradas (el backtest supone 10 pb por orden)")
    print(f"[ls] {'(simulacro, sin ordenes) ' if dry else ''}posiciones: {n_side[1]} largas / {n_side[-1]} cortas | candidatos: "
          f"{sum(1 for c in cands if c['side'] == 1)} largos, {sum(1 for c in cands if c['side'] == -1)} cortos")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--asof", help="sesion objetivo (solo con --dry-run)")
    a = ap.parse_args()
    if a.asof and not a.dry_run:
        raise SystemExit("--asof solo se admite con --dry-run")
    run(date.fromisoformat(a.asof) if a.asof else None, a.dry_run)
