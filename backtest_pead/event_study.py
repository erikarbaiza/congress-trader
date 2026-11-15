"""
Fase 2b: estudio de eventos del PEAD sobre cache/events_panel.pkl.

Estadistico: la media por evento y un t con error estandar AGRUPADO por mes de anuncio. Los anuncios de un mismo
trimestre estan correlacionados, asi que un t por evento sobrestimaria la significacion.
Retorno anormal = accion - SPY (ajuste de mercado simple, sin beta ni costes).

Uso:  python backtest_pead/event_study.py
"""
import sys

import numpy as np
import pandas as pd

from events import HORIZONS
from universe import CACHE

BOT = "(surprise_pct >= 5) & (est_abs >= 0.10)"
PERIODS = {"2005-2015 (cota optimista)": (2005, 2015), "2016-2026 (fiable)": (2016, 2026),
           "2022-2026 (menos sesgo)": (2022, 2026)}


def cluster_t(sub: pd.DataFrame, col: str) -> float:
    """t de la media por evento con error estandar agrupado por mes de anuncio."""
    d = sub.dropna(subset=[col])
    if len(d) < 30:
        return np.nan
    x = d[col].to_numpy()
    resid = pd.Series(x - x.mean()).groupby(d["month"].to_numpy()).sum()
    se = np.sqrt((resid ** 2).sum()) / len(x)
    return x.mean() / se if se > 0 else np.nan


def cell(sub: pd.DataFrame, col: str) -> str:
    x = sub[col].dropna()
    if len(x) < 30:
        return "      n/d"
    return f"{x.mean() * 100:+5.2f} ({cluster_t(sub, col):+4.1f})"


def spread_cell(a: pd.DataFrame, b: pd.DataFrame, col: str) -> str:
    """Diferencia de medias (a - b) con error estandar agrupado por mes (valido aunque a y b se solapen)."""
    da, db = a.dropna(subset=[col]), b.dropna(subset=[col])
    if min(len(da), len(db)) < 30:
        return "      n/d"
    ma, mb = da[col].mean(), db[col].mean()
    ra = (da[col] - ma).groupby(da["month"]).sum() / len(da)
    rb = (db[col] - mb).groupby(db["month"]).sum() / len(db)
    se = np.sqrt(((ra.sub(rb, fill_value=0)) ** 2).sum())
    return f"{(ma - mb) * 100:+5.2f} ({(ma - mb) / se:+4.1f})" if se > 0 else "      n/d"


def table(sub: pd.DataFrame, title: str) -> None:
    sub = sub.copy()
    sub["q"] = sub.groupby("year")["surprise_px"].transform(
        lambda s: pd.qcut(s, 5, labels=False, duplicates="drop") if s.notna().sum() >= 25 else np.nan)
    groups = {
        "TODOS los anuncios": sub,
        "REGLA DEL BOT (>=5 %, |est|>=0,10 $)": sub.query(BOT),
        "Sorpresa >= 5 % (sin filtro 0,10 $)": sub.query("surprise_pct >= 5"),
        "Q1 (peor sorpresa)": sub[sub.q == 0], "Q2": sub[sub.q == 1], "Q3": sub[sub.q == 2],
        "Q4": sub[sub.q == 3], "Q5 (mejor sorpresa)": sub[sub.q == 4],
    }
    print(f"\n{title}  | AR medio % vs SPY (t agrupado por mes)  | eventos con AR a 10d: {int(sub['ar_l1_h10'].notna().sum())}")
    print(f"{'':40}" + "".join(f"{'h=' + str(h) + 'd':>16}" for h in HORIZONS))
    for name, g in groups.items():
        print(f"{name:40}" + "".join(f"{cell(g, f'ar_l1_h{h}'):>16}" for h in HORIZONS) + f"   n={len(g)}")
    print(f"{'Q5 - Q1':40}" + "".join(f"{spread_cell(groups['Q5 (mejor sorpresa)'], groups['Q1 (peor sorpresa)'], f'ar_l1_h{h}'):>16}" for h in HORIZONS))
    print(f"{'REGLA DEL BOT - TODOS':40}" + "".join(f"{spread_cell(groups['REGLA DEL BOT (>=5 %, |est|>=0,10 $)'], groups['TODOS los anuncios'], f'ar_l1_h{h}'):>16}" for h in HORIZONS))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = pd.read_pickle(CACHE / "events_panel.pkl")
    print(f"Panel: {len(p)} eventos, {p.ticker.nunique()} empresas, {p.date.min().date()} a {p.date.max().date()}")
    for name, (a, b) in PERIODS.items():
        table(p[(p.year >= a) & (p.year <= b)], name)

    print("\nREGLA DEL BOT por anio  (AR % a 7 y a 20 dias habiles, entrada lag 1)")
    print(f"{'anio':6}{'n':>6}{'AR 7d':>9}{'acierto':>9}{'AR 20d':>9}{'TODOS 7d':>10}")
    bot = p.query(BOT)
    for y, g in bot.groupby("year"):
        a7, a20 = g["ar_l1_h7"].dropna(), g["ar_l1_h20"].dropna()
        all7 = p[p.year == y]["ar_l1_h7"].dropna()
        if len(a7) >= 10:
            print(f"{y:<6}{len(g):>6}{a7.mean() * 100:>+9.2f}{(a7 > 0).mean() * 100:>8.0f}%{a20.mean() * 100:>+9.2f}{all7.mean() * 100:>+10.2f}")

    print("\nSensibilidad al retraso de entrada, regla del bot, 2016-2026 (AR % a 10d):")
    r = bot[bot.year >= 2016]
    for lag in (1, 2):
        print(f"  entrada R+{lag}: {cell(r, f'ar_l{lag}_h10')}")

    print("\nFiltro de reaccion (idea de mejora): regla del bot con y sin reaccion positiva el dia del anuncio, 2016-2026, AR % a 10d")
    r = bot[bot.year >= 2016]
    print(f"  reaccion > 0: {cell(r[r.ear > 0], 'ar_l1_h10')}  n={int((r.ear > 0).sum())}")
    print(f"  reaccion <= 0: {cell(r[r.ear <= 0], 'ar_l1_h10')}  n={int((r.ear <= 0).sum())}")


if __name__ == "__main__":
    main()
