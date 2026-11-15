"""
Fase 1c: cobertura por anno = cuantos miembros del S&P 500 tienen datos usables (el sesgo de supervivencia medido).

Un miembro de un anno cuenta como:
  - con sorpresas: tiene al menos un evento con fecha dentro de ese anno
  - con precios:   su serie de precios cubre el 1 de julio de ese anno
Salida: tabla en pantalla y cache/coverage_by_year.csv

Uso:  python backtest_pead/coverage.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from universe import CACHE, load_universe, members_on   # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    u = load_universe()
    ev_years: dict[str, set[int]] = {}
    px_span: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for t in u["yf_ticker"].unique():
        e = CACHE / "events" / f"{t}.pkl"
        if e.exists():
            df = pd.read_pickle(e)
            if len(df):
                ev_years[t] = set(pd.DatetimeIndex(df.index).year)
        p = CACHE / "prices" / f"{t}.pkl"
        if p.exists():
            df = pd.read_pickle(p)
            if len(df):
                px_span[t] = (df.index.min(), df.index.max())

    rows = []
    for y in range(2005, 2027):
        day = pd.Timestamp(f"{y}-07-01")
        m = members_on(u, day)
        tk = m["yf_ticker"].unique()
        has_ev = [t for t in tk if y in ev_years.get(t, set())]
        has_px = [t for t in tk if t in px_span and px_span[t][0] <= day <= px_span[t][1]]
        both = set(has_ev) & set(has_px)
        rows.append({"anno": y, "miembros": len(tk), "con_sorpresas": len(has_ev), "con_precios": len(has_px),
                     "con_ambos": len(both), "cobertura_%": round(len(both) / max(len(tk), 1) * 100, 1)})
    out = pd.DataFrame(rows)
    out.to_csv(CACHE / "coverage_by_year.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
