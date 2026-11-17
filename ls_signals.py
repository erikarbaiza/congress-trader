"""
Senales del modulo largo/corto: anuncios de resultados del S&P 500 -> candidatos largos y cortos.

Replica la logica del backtest (backtest_pead/events.py y ls_sim.py):
  - dia de reaccion R: anuncio antes de apertura (hora < 12) -> ese mismo dia habil; despues de cierre -> siguiente dia habil
  - dia de entrada: R + 1 dia habil, en la apertura (el bot corre la noche anterior y deja la orden en cola)
  - sorpresa % = (real - estimado) / |estimado|;  sorpresa/precio = (real - estimado) / cierre previo a R * 100
  - LARGO: sorpresa/precio >= P80 y sorpresa % > 0;  CORTO: sorpresa/precio <= P20 y sorpresa % < 0
Los percentiles (4 anios previos) estan en ls_thresholds.json y se refrescan con backtest_pead/make_thresholds.py.
"""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from alpaca.trading.requests import GetCalendarRequest

from fetch_utils import run_in_batches
from ls_config import LS_LOOKBACK_DAYS, LS_MAX_ENTRY_LAG
from trader import _get_trading_client

THRESHOLDS_FILE = Path(__file__).parent / "ls_thresholds.json"
UNIVERSE_FILE = Path(__file__).parent / "ls_sp500.json"      # instantanea de respaldo si Wikipedia falla
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def sp500_tickers() -> list[str]:
    """Lista completa del S&P 500 (Wikipedia con User-Agent); si falla, la ultima instantanea guardada. Nunca una lista parcial."""
    try:
        html = requests.get(WIKI_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        tk = pd.read_html(io.StringIO(html))[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        if len(tk) > 400:
            UNIVERSE_FILE.write_text(json.dumps(sorted(tk)), encoding="utf-8")
            return tk
    except Exception as e:
        print(f"[ls] Wikipedia fallo ({e}); uso la instantanea guardada")
    return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))


def load_thresholds() -> dict:
    return json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))


def trading_calendar(start: date, end: date) -> list[date]:
    cal = _get_trading_client().get_calendar(GetCalendarRequest(start=start, end=end))
    return sorted(c.date for c in cal)


def reaction_day(wall: datetime, days: list[date]) -> date | None:
    """Dia en que el mercado reacciona al anuncio (antes de apertura: ese dia; despues de cierre: el siguiente)."""
    d = wall.date()
    later = [x for x in days if x > d]
    if wall.hour >= 12:
        return later[0] if later else None
    return d if d in days else (later[0] if later else None)


def next_day(d: date, days: list[date]) -> date | None:
    later = [x for x in days if x > d]
    return later[0] if later else None


def _events_for(t: str, since: date, attempts: int = 3, pause: float = 1.5) -> tuple[list[dict], bool]:
    """(eventos, ok). yfinance devuelve vacio sin avisar cuando se satura; un S&P 500 siempre tiene filas (aunque sean
    futuras), asi que un vacio persistente se trata como fallo de descarga (ok=False) y no como 'sin anuncios'."""
    ed = None
    for i in range(attempts):
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=8)
        except Exception:
            ed = None
        if ed is not None and not ed.empty:
            break
        time.sleep(pause * (i + 1))
    if ed is None or ed.empty:
        return [], False
    out = []
    for ts, r in ed.dropna(subset=["Reported EPS", "EPS Estimate"]).iterrows():
        wall = ts.to_pydatetime().replace(tzinfo=None)
        if wall.date() >= since:
            out.append({"ticker": t, "wall": wall, "est": float(r["EPS Estimate"]), "act": float(r["Reported EPS"])})
    return out, True


def _prev_close(t: str, r_day: date) -> float | None:
    for attempt in range(3):
        try:
            c = yf.Ticker(t).history(start=r_day - timedelta(days=10), end=r_day, auto_adjust=False)["Close"].dropna()
            if len(c):
                return float(c.iloc[-1])
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def official_price(t: str, day: date, field: str) -> float | None:
    """Apertura ('Open') o cierre ('Close') oficial de un dia ya terminado; None si no se pudo obtener."""
    for attempt in range(3):
        try:
            h = yf.Ticker(t).history(start=day, end=day + timedelta(days=1), auto_adjust=False)
            if len(h) and h.index[0].date() == day:
                return float(h[field].iloc[0])
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def find_candidates(asof: date, tickers: list[str] | None = None) -> list[dict]:
    """Candidatos largos (side=+1) y cortos (side=-1) cuyo dia de entrada es la sesion `asof` o la anterior
    (margen LS_MAX_ENTRY_LAG).
    Los que no se pueden evaluar (sin precio previo) se devuelven con side=0 y un motivo."""
    thr = load_thresholds()
    days = trading_calendar(asof - timedelta(days=40), asof + timedelta(days=40))
    if asof not in days:
        return []
    i = days.index(asof)
    allowed = set(days[max(0, i - LS_MAX_ENTRY_LAG): i + 1])
    tickers = tickers or sp500_tickers()
    since = asof - timedelta(days=LS_LOOKBACK_DAYS)

    first = run_in_batches(tickers, lambda t: _events_for(t, since))     # orden aleatorio y en dos tandas con pausa
    events = [e for _, (lst, _ok) in first for e in lst]
    failed = [t for t, (_, ok) in first if not ok]
    for rnd in (1, 2):                            # reintentos: enfriar, y luego uno a uno con pausa entre peticiones
        if not failed:
            break
        print(f"[ls] sin datos de resultados: {len(failed)} tickers; ronda de reintento {rnd}/2 tras {60 * rnd} s de espera")
        time.sleep(60 * rnd)
        still = []
        for t in failed:
            lst, ok = _events_for(t, since, attempts=2, pause=2.0)
            time.sleep(0.7)
            events += lst
            if not ok:
                still.append(t)
        failed = still
    if failed:
        print(f"[ls] AVISO: sin datos de resultados tras reintentar: {failed}")

    pre = []
    for e in events:
        if e["est"] == 0 or e["act"] == e["est"]:
            continue
        r = reaction_day(e["wall"], days)
        entry = next_day(r, days) if r else None
        if entry in allowed:
            e.update(reaction_day=r, entry_day=entry, surprise_pct=(e["act"] - e["est"]) / abs(e["est"]) * 100)
            pre.append(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        prices = list(pool.map(lambda e: _prev_close(e["ticker"], e["reaction_day"]), pre))

    out = [{"ticker": t, "side": 0, "reason": "sin datos de resultados (descarga fallida)", "strength": 0.0, "surprise_px": None,
            "surprise_pct": None, "event_date": since.isoformat(), "reaction_day": "", "entry_day": ""} for t in failed]
    for e, px in zip(pre, prices):
        if not px:       # nunca se descarta en silencio: queda registrado como no resuelto (side=0)
            out.append({"ticker": e["ticker"], "side": 0, "reason": "sin precio previo", "strength": 0.0, "surprise_px": None,
                        "surprise_pct": round(e["surprise_pct"], 2), "event_date": e["wall"].date().isoformat(),
                        "reaction_day": e["reaction_day"].isoformat(), "entry_day": e["entry_day"].isoformat()})
            continue
        spx = (e["act"] - e["est"]) / px * 100
        side = 1 if (spx >= thr["p80"] and e["surprise_pct"] > 0) else (-1 if (spx <= thr["p20"] and e["surprise_pct"] < 0) else 0)
        if side:
            out.append({"ticker": e["ticker"], "side": side, "strength": abs(spx), "surprise_px": round(spx, 4),
                        "surprise_pct": round(e["surprise_pct"], 2), "event_date": e["wall"].date().isoformat(),
                        "reaction_day": e["reaction_day"].isoformat(), "entry_day": e["entry_day"].isoformat()})
    return out
