"""
Fase 1a: universo historico del S&P 500 (point-in-time).

Fuente: dataset publico fja05680/sp500 (ticker, start_date, end_date), tratado solo como datos.
Salida: cache/universe.csv con una fila por periodo de pertenencia desde 2004.
Un mismo ticker puede aparecer varias veces (sale y vuelve a entrar) o haber sido reutilizado por otra empresa.

Uso:  python backtest_pead/universe.py
"""
import sys
from pathlib import Path

import pandas as pd
import requests

CACHE = Path(__file__).parent / "cache"
SRC = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
FIRST_DAY = pd.Timestamp("2004-01-01")


def load_universe(refresh: bool = False) -> pd.DataFrame:
    CACHE.mkdir(exist_ok=True)
    raw, out = CACHE / "universe_raw.csv", CACHE / "universe.csv"
    if out.exists() and not refresh:
        return pd.read_csv(out, parse_dates=["start", "end"])
    if refresh or not raw.exists():
        r = requests.get(SRC, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        raw.write_bytes(r.content)
    df = pd.read_csv(raw, parse_dates=["start_date", "end_date"]).rename(
        columns={"start_date": "start", "end_date": "end"})
    df = df[df["end"].isna() | (df["end"] >= FIRST_DAY)].copy()
    df["yf_ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    df.sort_values(["ticker", "start"]).to_csv(out, index=False)
    return pd.read_csv(out, parse_dates=["start", "end"])


def members_on(universe: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    return universe[(universe["start"] <= day) & (universe["end"].isna() | (universe["end"] >= day))]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    u = load_universe(refresh=True)
    active = u["end"].isna()
    print(f"Periodos de pertenencia desde 2004: {len(u)} | tickers distintos: {u['ticker'].nunique()}")
    print(f"Siguen en el indice: {int(active.sum())} | ya salieron: {int((~active).sum())}")
    for y in (2005, 2010, 2015, 2020, 2025):
        print(f"  miembros el 1-jul-{y}: {len(members_on(u, pd.Timestamp(f'{y}-07-01')))}")
