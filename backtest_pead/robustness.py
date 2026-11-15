"""
Fase 4: robustez de la regla actual del bot (V0). No evalua V1-V5 en 2021-2026 (esa ventana sigue sellada para ellas).

  A) Barrido de costes: 0 / 5 / 10 / 20 / 30 pb round-trip, en DISENO y PRUEBA
  B) Retorno con la beta cubierta (r - beta*SPY) y desglose por anio, con la cobertura de datos de cada anio
  C) Placebo: mismas fechas de entrada, mismo numero de operaciones y mismos tamanos que V0, pero la accion se sustituye
     por otra al azar de las que anunciaron resultados ese dia (N semillas, sin costes). Aisla la capacidad de seleccion.

Uso:  python backtest_pead/robustness.py [semillas]
"""
import sys

import numpy as np
import pandas as pd

from portfolio_sim import (CACHE, DESIGN_END, STOP, START, TEST_START, VARIANTS, candidates, load, metrics, ols_hac,
                           simulate)

sys.stdout.reconfigure(encoding="utf-8")
END = pd.Timestamp("2100-01-01")


def both_periods(eq, inv, tr, spy_close, dates, k0, kend):
    d = metrics(eq, inv, tr, spy_close, dates, START, DESIGN_END, k0)
    t = metrics(eq, inv, tr, spy_close, dates, TEST_START, dates[kend], k0)
    return d, t


def main(n_seeds: int) -> None:
    panel, spy, dates, data = load()
    spy_close = spy["Close"].to_numpy()
    v0 = VARIANTS["V0"]
    cands = candidates(panel, "bot")
    run = lambda c, cost: simulate(c, dates, data, v0["H"], v0["stop"], v0["sizing"], cost, END)

    print("A) BARRIDO DE COSTES (V0 = regla actual del bot)")
    print(f"{'coste RT':>9} | {'DISENO 2005-2020: CAGR':>24}{'alpha % (t)':>16} | {'PRUEBA 2021-2026: CAGR':>24}{'alpha % (t)':>16}")
    keep = {}
    for bp in (0, 5, 10, 20, 30):
        out = run(cands, bp / 1e4)
        d, t = both_periods(*out[:3], spy_close, dates, out[3], out[4])
        keep[bp] = out
        print(f"{bp:>7}pb | {d['cagr'] * 100:>23.1f}%{d['alpha'] * 100:>+10.2f} ({d['t']:+.1f}) | {t['cagr'] * 100:>23.1f}%{t['alpha'] * 100:>+10.2f} ({t['t']:+.1f})")
    print(f"  SPY: diseno CAGR {d['spy_cagr'] * 100:+.1f}% | prueba CAGR {t['spy_cagr'] * 100:+.1f}%")

    print("\nB) DESGLOSE POR ANIO a 10 pb (beta cubierta con la beta de cada periodo)")
    eq, inv, tr, k0, kend = keep[10]
    cov = pd.read_csv(CACHE / "coverage_by_year.csv").set_index("anno")["cobertura_%"]
    yrs = pd.Series(dates.year, index=range(len(dates)))
    print(f"{'anio':>5}{'bot %':>8}{'SPY %':>8}{'expo':>6}{'ops':>5}{'neto/op':>9}{'cobertura datos':>17}")
    for y in range(2005, 2027):
        ks = yrs[yrs == y].index
        ks = [k for k in ks if k0 <= k <= kend]
        if len(ks) < 100:
            continue
        a, b = ks[0] - 1 if ks[0] > k0 else ks[0], ks[-1]
        tr_y = [x[3] for x in tr if ks[0] <= x[2] <= b]
        print(f"{y:>5}{(eq[b] / eq[a] - 1) * 100:>+8.1f}{(spy_close[b] / spy_close[a] - 1) * 100:>+8.1f}{inv[ks[0]:b + 1].mean() * 100:>5.0f}%"
              f"{len(tr_y):>5}{np.mean(tr_y) * 100 if tr_y else float('nan'):>+8.2f}%{cov.get(y, float('nan')):>16.0f}%")
    print("\n   Retorno con beta cubierta (r - beta*SPY), 10 pb:")
    for name, (a, b) in {"diseno": (START, DESIGN_END), "prueba": (TEST_START, dates[kend])}.items():
        ka, kb = max(int(dates.searchsorted(a)), k0 + 1), int(dates.searchsorted(b, side="right")) - 1
        r = eq[ka:kb + 1] / eq[ka - 1:kb] - 1
        s = spy_close[ka:kb + 1] / spy_close[ka - 1:kb] - 1
        beta = ols_hac(r, s)[0][1]
        h = r - beta * s
        print(f"   {name}: beta {beta:.2f} | retorno anual cubierto {h.mean() * 252 * 100:+.2f}% | vol {h.std() * np.sqrt(252) * 100:.1f}% | Sharpe {h.mean() / h.std() * np.sqrt(252):+.2f}")

    print(f"\nC) PLACEBO ({n_seeds} semillas, sin costes): mismas fechas, numero y tamanos; la accion se sustituye al azar")
    pool = panel.groupby("entry_l1")["ticker"].apply(np.array).to_dict()
    ref_d, ref_t = both_periods(*keep[0][:3], spy_close, dates, keep[0][3], keep[0][4])
    res = {"d": [], "t": []}
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        c = cands.copy()
        c["ticker"] = [rng.choice(pool[d]) for d in c["entry_l1"]]
        out = run(c, 0.0)
        d_, t_ = both_periods(*out[:3], spy_close, dates, out[3], out[4])
        res["d"].append((d_["alpha"], d_["cagr"], d_["avg"]))
        res["t"].append((t_["alpha"], t_["cagr"], t_["avg"]))
        print(f"   semilla {seed + 1}/{n_seeds}", end="\r", flush=True)
    print(" " * 30)
    for key, name, ref in (("d", "DISENO 2005-2020", ref_d), ("t", "PRUEBA 2021-2026", ref_t)):
        a = np.array(res[key])
        pct = (a[:, 0] < ref["alpha"]).mean() * 100
        print(f"   {name}: V0 alpha {ref['alpha'] * 100:+.2f}% (CAGR {ref['cagr'] * 100:+.1f}%, bruto/op {ref['avg'] * 100:+.2f}%) | "
              f"placebo alpha {a[:, 0].mean() * 100:+.2f}% +/- {a[:, 0].std() * 100:.2f} (CAGR {a[:, 1].mean() * 100:+.1f}%, bruto/op {a[:, 2].mean() * 100:+.2f}%) "
              f"| V0 supera al {pct:.0f}% de los placebos")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
