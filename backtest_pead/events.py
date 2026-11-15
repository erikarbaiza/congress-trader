"""
Fase 2a: panel de eventos (una fila por anuncio de resultados de un miembro del S&P 500 en su periodo de pertenencia).

Temporizacion (la hora de publicacion de yfinance distingue antes/despues de mercado):
  - antes de apertura (hora < 12): el mercado reacciona ese mismo dia  -> dia de reaccion R = dia del anuncio
  - despues de cierre (hora >= 12): reacciona al dia habil siguiente    -> R = siguiente dia habil
  - entrada base (lag 1): apertura del dia R+1; variante (lag 2): apertura de R+2. Replica al bot, que detecta
    el anuncio al cierre y compra en la siguiente apertura, y deja margen por si yfinance tarda en publicarlo.
  - salida: cierre del dia (entrada + h dias habiles), h en HORIZONS
  - retorno anormal (ar) = retorno de la accion - retorno del SPY en la misma ventana (ajuste de mercado simple)

Sorpresa: surprise_pct = (real - estimado) / |estimado| (regla del bot) y surprise_px = (real - estimado) / precio previo
(robusta a splits). El precio de yfinance esta ajustado por dividendos, asi que surprise_px sobrestima algo en
empresas con mucho dividendo en los anios antiguos; solo se usa para ordenar en quintiles dentro de cada anio.

Uso:  python backtest_pead/events.py      -> cache/events_panel.pkl
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from universe import CACHE, load_universe

HORIZONS = [1, 5, 7, 10, 20, 40]
LAGS = (1, 2)


def _prices(t: str) -> pd.DataFrame | None:
    p = CACHE / "prices" / f"{t}.pkl"
    if not p.exists():
        return None
    df = pd.read_pickle(p)
    if df.empty:
        return None
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.dropna(subset=["Open", "Close"])


def build_panel() -> pd.DataFrame:
    u = load_universe()
    spy = _prices("SPY")
    s_open, s_close = spy["Open"].to_numpy(), spy["Close"].to_numpy()
    rows = []
    for t, g in u.groupby("yf_ticker"):
        ep = CACHE / "events" / f"{t}.pkl"
        px = _prices(t)
        if px is None or not ep.exists():
            continue
        ev = pd.read_pickle(ep)
        if ev.empty:
            continue
        ev = ev[~ev.index.duplicated(keep="first")]
        windows = [(r.start, r.end if pd.notna(r.end) else pd.Timestamp.max) for r in g.itertuples()]
        n = len(px)
        o, c = px["Open"].to_numpy(), px["Close"].to_numpy()
        spy_pos = spy.index.get_indexer(px.index)          # posicion en SPY de cada dia de la accion (-1 si falta)

        def sp(i, arr):
            return arr[spy_pos[i]] if spy_pos[i] >= 0 else np.nan

        for ts, r in ev.iterrows():
            wall = ts.replace(tzinfo=None)
            day = wall.normalize()
            if not any(a <= day <= b for a, b in windows):
                continue                                    # el ticker no era del indice ese dia (o fue reutilizado)
            est, act = r["EPS Estimate"], r["Reported EPS"]
            if pd.isna(est) or pd.isna(act):
                continue
            i0 = px.index.searchsorted(day)
            is_td = i0 < n and px.index[i0] == day
            ri = i0 + (1 if (wall.hour >= 12 and is_td) else 0)
            if ri < 1 or ri >= n:
                continue
            rec = {
                "ticker": t, "date": day, "year": day.year, "month": day.strftime("%Y-%m"),
                "amc": wall.hour >= 12, "est": est, "act": act, "est_abs": abs(est),
                "surprise_pct": (act - est) / abs(est) * 100 if est != 0 else np.nan,
                "surprise_px": (act - est) / c[ri - 1] * 100,
                "ear": (c[ri] / c[ri - 1] - 1) - (sp(ri, s_close) / sp(ri - 1, s_close) - 1),
                "entry_l1": px.index[ri + 1] if ri + 1 < n else pd.NaT,
            }
            for lag in LAGS:
                e = ri + lag
                if e >= n or spy_pos[e] < 0:
                    continue
                for h in HORIZONS:
                    x = e + h
                    if x >= n or spy_pos[x] < 0:
                        continue
                    ret = c[x] / o[e] - 1
                    rec[f"ar_l{lag}_h{h}"] = ret - (s_close[spy_pos[x]] / s_open[spy_pos[e]] - 1)
                    if lag == 1:
                        rec[f"ret_l1_h{h}"] = ret
            rows.append(rec)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    panel = build_panel()
    panel.to_pickle(CACHE / "events_panel.pkl")
    print(f"Eventos: {len(panel)} | tickers: {panel.ticker.nunique()} | {panel.date.min().date()} a {panel.date.max().date()}")
    print(f"Despues de cierre: {panel.amc.mean() * 100:.0f}% | con AR a 10d (lag 1): {int(panel['ar_l1_h10'].notna().sum())}")
    print(panel.groupby("year").size().to_string())
