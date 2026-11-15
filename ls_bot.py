"""
Modulo paper trading largo/corto: hipotesis preregistrada, especificacion congelada (ver ls_config.py).

Corre una vez al dia tras el bot PEAD, en una cuenta Alpaca paper APARTE (ALPACA_LS_KEY / ALPACA_LS_SECRET):
  1. concilia precios de salida con las ejecuciones reales
  2. cierra las posiciones cuya fecha de salida ya llego (20 dias habiles tras la entrada)
  3. abre largos / cortos (5 % del patrimonio cada uno, max 10 por lado) para los anuncios cuyo dia de entrada es hoy
     (o ayer, si el bot no corrio); los cortos solo si Alpaca los marca shortable y easy_to_borrow
  4. registra el dia en ls_equity.csv; todo lo no ejecutado queda en ls_missed.csv con su motivo

Archivos de estado: ls_state.json, ls_closed.csv, ls_equity.csv, ls_missed.csv.
Seguridad: se niega a operar si la cuenta es la misma que la del bot PEAD (su stop-loss trata los cortos como largos).

Uso:  python ls_bot.py [--dry-run] [--asof AAAA-MM-DD]     (--asof solo con --dry-run)
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
from ls_signals import find_candidates, sp500_tickers, trading_calendar
from trader import _get_data_client, _get_trading_client

ROOT = Path(__file__).parent
STATE, CLOSED, EQUITY, MISSED = (ROOT / n for n in ("ls_state.json", "ls_closed.csv", "ls_equity.csv", "ls_missed.csv"))
CLOSED_COLS = ["ticker", "side", "event_date", "entry_date", "exit_date", "days_held", "entry_price", "exit_price",
               "return_pct", "surprise_px", "surprise_pct", "exit_price_source"]
EQUITY_COLS = ["date", "equity", "spy_price", "n_long", "n_short", "long_mv", "short_mv"]
MISSED_COLS = ["date", "ticker", "side", "reason", "surprise_px", "event_date"]


# ── utilidades ───────────────────────────────────────────────────────────────
def _ls_client() -> TradingClient | None:
    if not (LS_KEY and LS_SECRET):
        return None
    client = TradingClient(LS_KEY, LS_SECRET, paper=True)     # siempre paper
    if client.get_account().account_number == _get_trading_client().get_account().account_number:
        raise SystemExit("[ls] ABORTADO: ALPACA_LS_KEY apunta a la misma cuenta que el bot PEAD. Crea una cuenta paper aparte.")
    return client


def _alp(ticker: str) -> str:
    """yfinance escribe BRK-B; Alpaca escribe BRK.B."""
    return ticker.replace("-", ".")


def _last_price(symbol: str) -> float:
    r = _get_data_client().get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))
    return float(r[symbol].price)


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"positions": {}, "seen": []}


def _append(path: Path, cols: list[str], row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in cols})


def _missed(asof: date, c: dict, reason: str, dry: bool) -> None:
    print(f"  [ls] NO EJECUTADO {c['ticker']:6} {({1: 'LARGO', -1: 'CORTO'}).get(c['side'], 'sin lado')} — {reason}")
    if not dry:
        _append(MISSED, MISSED_COLS, {"date": asof.isoformat(), "ticker": c["ticker"], "side": c["side"], "reason": reason,
                                      "surprise_px": c.get("surprise_px"), "event_date": c["event_date"]})


# ── conciliacion de precios de salida ────────────────────────────────────────
def reconcile(client: TradingClient) -> int:
    """Sustituye el precio de salida estimado por la ejecucion real (cubrir un corto = compra; cerrar un largo = venta)."""
    if not CLOSED.exists():
        return 0
    with open(CLOSED, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n = 0
    for r in rows:
        if r["exit_price_source"] == "fill":
            continue
        side = OrderSide.SELL if r["side"] == "1" else OrderSide.BUY
        since = datetime.fromisoformat(r["exit_date"]).replace(tzinfo=timezone.utc)
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[_alp(r["ticker"])], side=side,
                                                    after=since, limit=50))
        fills = [o for o in orders if o.filled_at and o.filled_avg_price]
        if not fills:
            continue
        o = min(fills, key=lambda x: x.filled_at)
        entry, px = float(r["entry_price"]), float(o.filled_avg_price)
        r["exit_price"], r["exit_price_source"] = f"{px:.2f}", "fill"
        r["return_pct"] = f"{(px / entry - 1) * int(r['side']):.4f}"
        r["exit_date"] = o.filled_at.strftime("%Y-%m-%d")
        n += 1
    if n:
        with open(CLOSED, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CLOSED_COLS)
            w.writeheader()
            w.writerows(rows)
    return n


# ── ciclo diario ─────────────────────────────────────────────────────────────
def run(asof: date, dry: bool) -> None:
    client = _ls_client()
    if client is None and not dry:
        print("[ls] ALPACA_LS_KEY / ALPACA_LS_SECRET no configuradas: modulo largo/corto desactivado.")
        return
    cal = trading_calendar(asof, asof + timedelta(days=60))
    if not cal or cal[0] != asof:
        print(f"[ls] {asof} no es dia de mercado.")
        return
    exit_date = cal[LS_HOLD_DAYS]          # entrada = dia 0; salida al cierre del dia habil 20, como en el backtest
    equity = float(client.get_account().equity) if client else 100_000.0
    state = _load_state()
    live = {p.symbol: p for p in client.get_all_positions()} if client else {}
    if client and not dry:
        n = reconcile(client)
        if n:
            print(f"[ls] diario: {n} precio(s) de salida sustituido(s) por la ejecucion real")

    # 1. salidas
    for t, p in list(state["positions"].items()):
        if date.fromisoformat(p["exit_date"]) > asof:
            continue
        lp = live.get(_alp(t))
        if lp is None:
            print(f"  [ls] {t}: sin posicion en la cuenta (orden de entrada no ejecutada); se retira del estado")
            state["positions"].pop(t)
            continue
        entry_px, last = float(lp.avg_entry_price), float(lp.current_price)
        print(f"  [ls] SALIDA {t:6} {'LARGO' if p['side'] == 1 else 'CORTO'} — {LS_HOLD_DAYS} dias habiles cumplidos")
        if not dry:
            client.close_position(_alp(t))
            _append(CLOSED, CLOSED_COLS, {
                "ticker": t, "side": p["side"], "event_date": p["event_date"], "entry_date": p["entry_date"],
                "exit_date": asof.isoformat(), "days_held": (asof - date.fromisoformat(p["entry_date"])).days,
                "entry_price": f"{entry_px:.2f}", "exit_price": f"{last:.2f}",
                "return_pct": f"{(last / entry_px - 1) * p['side']:.4f}", "surprise_px": p["surprise_px"],
                "surprise_pct": p["surprise_pct"], "exit_price_source": "estimate"})
            state["positions"].pop(t)

    # 2. entradas
    cands = find_candidates(asof, sp500_tickers())
    for c in [c for c in cands if c["side"] == 0]:
        _missed(asof, c, c.get("reason", "no resuelto"), dry)
    n_side = {1: sum(1 for p in state["positions"].values() if p["side"] == 1),
              -1: sum(1 for p in state["positions"].values() if p["side"] == -1)}
    for side in (1, -1):
        for c in sorted((c for c in cands if c["side"] == side), key=lambda x: -x["strength"]):
            t, key = c["ticker"], f"{c['ticker']}|{c['event_date']}"
            if key in state["seen"] or t in state["positions"]:
                continue
            if n_side[side] >= LS_MAX_PER_SIDE:
                _missed(asof, c, f"tope de {LS_MAX_PER_SIDE} posiciones por lado", dry)
                state["seen"].append(key)
                continue
            try:
                if side == -1:
                    a = _get_trading_client().get_asset(_alp(t))
                    if not (a.shortable and a.easy_to_borrow):
                        _missed(asof, c, "no se puede vender en corto (shortable/easy_to_borrow)", dry)
                        state["seen"].append(key)
                        continue
                price = _last_price(_alp(t))
                qty = math.floor(equity * LS_POSITION_PCT / price)
                if qty < 1:
                    _missed(asof, c, "cantidad < 1 accion", dry)
                    state["seen"].append(key)
                    continue
                print(f"  [ls] ENTRADA {t:6} {'LARGO' if side == 1 else 'CORTO'} x{qty} (~{qty * price:,.0f} $) — sorpresa/precio "
                      f"{c['surprise_px']:+.3f}%, sorpresa {c['surprise_pct']:+.1f}%")
                if not dry:
                    client.submit_order(MarketOrderRequest(symbol=_alp(t), qty=qty, side=OrderSide.BUY if side == 1 else OrderSide.SELL,
                                                           time_in_force=TimeInForce.DAY))
                    state["positions"][t] = {"side": side, "event_date": c["event_date"], "entry_date": asof.isoformat(),
                                             "exit_date": exit_date.isoformat(), "surprise_px": c["surprise_px"],
                                             "surprise_pct": c["surprise_pct"], "qty": qty}
                    state["seen"].append(key)
            except Exception as e:
                # un fallo en un valor no para el ciclo; si es "activo no encontrado" no se reintenta, si no, si
                _missed(asof, c, f"error: {str(e)[:80]}", dry)
                if "not found" in str(e).lower():
                    state["seen"].append(key)
                continue
            n_side[side] += 1

    # 3. registro del dia
    if client and not dry:
        pos = client.get_all_positions()
        mv = [float(p.market_value) for p in pos]
        _append(EQUITY, EQUITY_COLS, {"date": asof.isoformat(), "equity": f"{float(client.get_account().equity):.2f}",
                                      "spy_price": f"{_last_price('SPY'):.2f}", "n_long": sum(1 for x in mv if x > 0),
                                      "n_short": sum(1 for x in mv if x < 0), "long_mv": f"{sum(x for x in mv if x > 0):.2f}",
                                      "short_mv": f"{sum(x for x in mv if x < 0):.2f}"})
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        reconcile(client)
    print(f"[ls] {'(simulacro, sin ordenes) ' if dry else ''}posiciones: {n_side[1]} largas / {n_side[-1]} cortas | candidatos hoy: "
          f"{sum(1 for c in cands if c['side'] == 1)} largos, {sum(1 for c in cands if c['side'] == -1)} cortos")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--asof")
    a = ap.parse_args()
    if a.asof and not a.dry_run:
        raise SystemExit("--asof solo se admite con --dry-run")
    run(date.fromisoformat(a.asof) if a.asof else date.today(), a.dry_run)
