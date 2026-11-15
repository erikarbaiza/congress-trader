"""
Fase 0 del backtest PEAD: prueba de viabilidad de los datos (no es un resultado).

Mide, con una muestra pequena:
  1. Cobertura de sorpresas de BPA en yfinance (profundidad, estimados que faltan, hora de publicacion)
  2. Supervivencia: que pasa con empresas que ya no estan en el S&P 500
  3. Cobertura de precios diarios
  4. Si la tabla de cambios de Wikipedia permite reconstruir el indice por fecha
  5. Prueba de humo del estudio de eventos (retorno anormal a 10 dias tras sorpresa >= 5 %)

Uso (desde la raiz del repo):  python backtest_pead/phase0_spike.py
No toca el bot ni escribe fuera de backtest_pead/.
"""
import io
import sys
import time
import warnings

import numpy as np
import pandas as pd
import requests
import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")

SURVIVORS = ["AAPL", "MSFT", "JPM", "XOM", "JNJ", "GE", "AMD", "SBUX", "MCD", "PFE", "AMGN", "ZTS",
             "DUK", "SO", "CB", "ELV", "NOC", "RTX", "ISRG", "CAT", "HD", "KO", "PG", "NVDA", "GS",
             "UNH", "LMT", "DE", "HON", "TXN"]
EX_MEMBERS = ["SIVB", "FRC", "TWTR", "CTXS", "ATVI", "XLNX", "CERN", "ALXN", "ABMD", "DISH", "PBCT", "NLSN"]
START = "2004-01-01"
MIN_EST_USD = 0.10      # filtro actual del bot
HOLD_DAYS = 10          # dias naturales, como el bot
SURPRISE_MIN = 5.0      # %


def fetch_events(sym: str) -> pd.DataFrame | None:
    try:
        ed = yf.Ticker(sym).get_earnings_dates(limit=100)
    except Exception:
        return None
    if ed is None or ed.empty:
        return None
    ed = ed.dropna(subset=["Reported EPS"]).sort_index().copy()
    ed["ticker"] = sym
    return ed if len(ed) else None


def coverage_table(symbols: list[str]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows, events = [], {}
    for s in symbols:
        ev = fetch_events(s)
        time.sleep(0.4)
        if ev is None:
            rows.append({"ticker": s, "eventos": 0})
            continue
        events[s] = ev
        rows.append({
            "ticker": s, "eventos": len(ev),
            "desde": ev.index.min().date(), "hasta": ev.index.max().date(),
            "sin_estimado_%": round(ev["EPS Estimate"].isna().mean() * 100, 1),
            "|est|<0,10$_%": round((ev["EPS Estimate"].abs() < MIN_EST_USD).mean() * 100, 1),
        })
    return pd.DataFrame(rows), events


def price_panel(symbols: list[str]) -> dict[str, pd.DataFrame]:
    raw = yf.download(symbols + ["SPY"], start=START, auto_adjust=True, group_by="ticker",
                      progress=False, threads=True)
    out = {}
    for s in symbols + ["SPY"]:
        try:
            df = raw[s].dropna(subset=["Close"])
        except Exception:
            continue
        if len(df):
            out[s] = df
    return out


def sp500_changes() -> pd.DataFrame | None:
    try:
        html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        tables = pd.read_html(io.StringIO(html))
        ch = tables[1]
        ch.columns = ["_".join(map(str, c)).strip() for c in ch.columns]
        return ch
    except Exception as e:
        print("  [wikipedia] fallo:", e)
        return None


def smoke_event_study(events: dict[str, pd.DataFrame], px: dict[str, pd.DataFrame]) -> pd.DataFrame:
    spy = px["SPY"]
    recs = []
    for s, ev in events.items():
        if s not in px:
            continue
        p = px[s]
        for dt, r in ev.iterrows():
            est, act = r["EPS Estimate"], r["Reported EPS"]
            if pd.isna(est) or est == 0:
                continue
            day = pd.Timestamp(dt).tz_localize(None).normalize()
            idx = p.index.searchsorted(day, side="right")      # 1er dia habil posterior al anuncio
            entry_i = idx + 1                                    # apertura de T+2: lag conservador
            if entry_i >= len(p):
                continue
            entry_day = p.index[entry_i]
            exit_day = entry_day + pd.Timedelta(days=HOLD_DAYS)
            j = p.index.searchsorted(exit_day)
            if j >= len(p) or entry_day < spy.index[0]:
                continue
            ret = p["Close"].iloc[j] / p["Open"].iloc[entry_i] - 1
            k0, k1 = spy.index.searchsorted(entry_day), spy.index.searchsorted(exit_day)
            if k1 >= len(spy):
                continue
            spy_ret = spy["Close"].iloc[k1] / spy["Open"].iloc[k0] - 1
            prev_close = p["Close"].iloc[max(idx - 1, 0)]
            recs.append({
                "ticker": s, "fecha": day, "surprise_pct": (act - est) / abs(est) * 100,
                "surprise_px": (act - est) / prev_close * 100,       # escalada por precio (robusta a splits)
                "est_abs": abs(est), "ret": ret, "ar": ret - spy_ret,
            })
    return pd.DataFrame(recs)


def main() -> None:
    print("=" * 70, "\n1) COBERTURA DE SORPRESAS (empresas actuales)\n", "=" * 70, sep="")
    cov_s, ev_s = coverage_table(SURVIVORS)
    print(cov_s.to_string(index=False))
    ok = cov_s[cov_s.eventos > 0]
    print(f"\nCon datos: {len(ok)}/{len(cov_s)} | eventos totales: {int(ok.eventos.sum())} | "
          f"mediana de eventos por empresa: {ok.eventos.median():.0f} | desde: {ok['desde'].min()}")
    print(f"Eventos con |estimado| < 0,10 $ (los descartaria el filtro actual): "
          f"{(pd.concat(ev_s.values())['EPS Estimate'].abs() < MIN_EST_USD).mean() * 100:.1f}%")
    hrs = pd.Series([i.hour for e in ev_s.values() for i in e.index]).value_counts().head(5)
    print("Hora de publicacion (hora local de EEUU) mas frecuente:", dict(hrs))

    print("\n" + "=" * 70, "\n2) SUPERVIVENCIA (antiguos miembros del S&P 500)\n", "=" * 70, sep="")
    cov_x, ev_x = coverage_table(EX_MEMBERS)
    print(cov_x.to_string(index=False))
    print(f"\nAntiguos miembros con datos de sorpresas: {int((cov_x.eventos > 0).sum())}/{len(cov_x)}")

    print("\n" + "=" * 70, "\n3) PRECIOS DIARIOS\n", "=" * 70, sep="")
    px = price_panel(SURVIVORS + EX_MEMBERS)
    got_s = [s for s in SURVIVORS if s in px]
    got_x = [s for s in EX_MEMBERS if s in px]
    print(f"Actuales con precios: {len(got_s)}/{len(SURVIVORS)} | antiguos con precios: {len(got_x)}/{len(EX_MEMBERS)} {got_x}")
    print("Primer dia de precios (SPY):", px["SPY"].index.min().date())

    print("\n" + "=" * 70, "\n4) TABLA DE CAMBIOS DEL INDICE (Wikipedia)\n", "=" * 70, sep="")
    ch = sp500_changes()
    if ch is not None:
        print(f"Filas: {len(ch)} | columnas: {list(ch.columns)}")
        date_col = ch.columns[0]
        d = pd.to_datetime(ch[date_col], errors="coerce")
        print(f"Cambios desde {d.min().date()} hasta {d.max().date()} | desde 2005: {(d >= '2005-01-01').sum()}")
        rem = [c for c in ch.columns if "Removed_Ticker" in c]
        if rem:
            print(f"Filas con ticker retirado indicado: {int(ch[rem[0]].notna().sum())}")

    print("\n" + "=" * 70, "\n5) PRUEBA DE HUMO DEL ESTUDIO DE EVENTOS (NO ES UN RESULTADO)\n", "=" * 70, sep="")
    ev = smoke_event_study(ev_s, px)
    print(f"Eventos con retorno calculable: {len(ev)}")
    if len(ev):
        beat = ev[(ev.surprise_pct >= SURPRISE_MIN) & (ev.est_abs >= MIN_EST_USD)]
        rest = ev.drop(beat.index)
        print(f"Regla actual del bot (sorpresa >= 5 % y |est| >= 0,10 $): n={len(beat)} | "
              f"AR medio 10d {beat.ar.mean() * 100:+.2f}% | mediana {beat.ar.median() * 100:+.2f}% | "
              f"acierto {(beat.ar > 0).mean() * 100:.0f}%")
        print(f"Resto de eventos:                                   n={len(rest)} | "
              f"AR medio 10d {rest.ar.mean() * 100:+.2f}% | mediana {rest.ar.median() * 100:+.2f}%")
        ev["decil"] = pd.qcut(ev.surprise_px, 5, labels=False, duplicates="drop")
        print("\nPor quintil de sorpresa (escalada por precio), AR medio a 10d:")
        print((ev.groupby("decil").agg(n=("ar", "size"), ar_medio_pct=("ar", lambda x: x.mean() * 100),
                                       mediana_pct=("ar", lambda x: x.median() * 100))).round(2).to_string())
        se = beat.ar.std(ddof=1) / np.sqrt(len(beat)) if len(beat) > 1 else float("nan")
        print(f"\nError estandar del AR medio de la regla actual: {se * 100:.2f} pp "
              "(con 30 empresas supervivientes; sesgo de supervivencia sin corregir)")


if __name__ == "__main__":
    main()
