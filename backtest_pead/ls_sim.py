"""
Hipotesis preregistrada: PEAD largo/corto sobre quintiles de sorpresa, 20 dias habiles.
(Escrito ANTES de ejecutar ninguna simulacion de esta hipotesis.)

ESPECIFICACION UNICA (sin variantes):
  Senal    sorpresa/precio (surprise_px) contra los percentiles 20 y 80 de los 4 anios previos (sin mirar el futuro)
           LARGO: surprise_px >= P80 y sorpresa % > 0      CORTO: surprise_px <= P20 y sorpresa % < 0
  Entrada  apertura del dia R+1 (igual que en las fases 2-4); salida: cierre del dia (entrada + 20 dias habiles)
  Tamano   5 % del patrimonio por posicion, max 10 largos y 10 cortos (bruto <= 100 %, neto ~ 0); si hay mas candidatos
           se eligen los de mayor |surprise_px|. Sin stop-loss. Sin apalancamiento adicional.
  Costes   PRINCIPAL: 10 pb round-trip por pata + 0,5 % anual de coste de prestamo sobre el nominal corto.
           Sensibilidad (diagnostico): costes 5/10/30 pb x prestamo 0,25/0,5/2 %. Efectivo y recompra sin rendimiento.
  Metricas alpha anual y beta frente al SPY (regresion diaria, t Newey-West), Sharpe, max drawdown; ademas cada pata por separado.

CRITERIO (fijado antes):
  1) DISENO 2005-2020, caso principal: alpha anual neto > 0 con t > 2. Si no se cumple, la hipotesis se RECHAZA y no se
     toca la ventana de prueba.
  2) Solo si pasa 1): PRUEBA 2021-2026 una sola vez: alpha neto > 0 con t > 2. Si no, se rechaza.
  3) Aviso preregistrado: la hipotesis nacio de la fase 2, que ya miro 2016-2026 de forma descriptiva; un resultado positivo
     en PRUEBA es confirmatorio pero no completamente independiente. La validacion limpia sera hacia delante.

Uso:
  python backtest_pead/ls_sim.py design
  python backtest_pead/ls_sim.py test      (solo si el diseno cumple el criterio)
"""
import sys

import numpy as np
import pandas as pd

from portfolio_sim import DESIGN_END, START, TEST_START, load, ols_hac

sys.stdout.reconfigure(encoding="utf-8")
H, POS_PCT, MAX_SIDE = 20, 0.05, 10
BASE_COST, BASE_BORROW = 0.0010, 0.005


def rolling_pcts(panel: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    d = panel.sort_values("date")
    dts, vals = d["date"].to_numpy(), d["surprise_px"].to_numpy()
    lo_, hi_ = {}, {}
    for day in pd.unique(d["date"]):
        a = np.searchsorted(dts, day - np.timedelta64(4 * 365, "D"), side="left")
        b = np.searchsorted(dts, day, side="left")
        w = vals[a:b]
        w = w[~np.isnan(w)]
        ok = len(w) >= 300
        lo_[day] = np.percentile(w, 20) if ok else np.nan
        hi_[day] = np.percentile(w, 80) if ok else np.nan
    return d["date"].map(lo_).reindex(panel.index), d["date"].map(hi_).reindex(panel.index)


def build_candidates(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["p20"], p["p80"] = rolling_pcts(p)
    long_ = p[(p.surprise_px >= p.p80) & (p.surprise_pct > 0)].assign(side=1)
    short = p[(p.surprise_px <= p.p20) & (p.surprise_pct < 0)].assign(side=-1)
    c = pd.concat([long_, short])
    c["strength"] = c["surprise_px"].abs()
    return c


def simulate(cands, dates, data, cost, borrow, end, sides=(1, -1), tickers=None):
    k0 = int(dates.searchsorted(START))
    k_end = int(dates.searchsorted(end, side="right")) - 1
    by_k: dict[int, list] = {}
    idx = dates.get_indexer(cands["entry_l1"])
    tk = cands["ticker"].to_numpy() if tickers is None else tickers
    for k, s, t, side in zip(idx, cands["strength"].to_numpy(), tk, cands["side"].to_numpy()):
        if k >= k0 and side in sides:
            by_k.setdefault(k, []).append((s, t, side))
    cash, pos = 100_000.0, {}
    eq = np.full(len(dates), np.nan)
    gross, net = np.zeros(len(dates)), np.zeros(len(dates))
    trades = []

    def close_pos(t, price, k):
        nonlocal cash
        p = pos.pop(t)
        cash += p["sh"] * price - abs(p["sh"] * price) * cost / 2
        ret = (price / p["ep"] - 1) * p["side"] - cost
        trades.append((t, p["side"], p["k"], k, ret))

    for k in range(k0, k_end + 1):
        eq_prev = cash + sum(p["sh"] * p["last"] for p in pos.values())
        for side in sides:
            n_side = sum(1 for p in pos.values() if p["side"] == side)
            for s, t, sd in sorted((x for x in by_k.get(k, []) if x[2] == side), reverse=True):
                if n_side >= MAX_SIDE:
                    break
                if t in pos:
                    continue
                o, c, ps, _ = data[t]
                if ps[k] < 0:
                    continue
                val = eq_prev * POS_PCT
                price = o[ps[k]]
                cash -= side * val + val * cost / 2
                pos[t] = dict(sh=side * val / price, ep=price, k=k, exit_k=k + H, last=price, side=side)
                n_side += 1
        for t in list(pos):
            p = pos[t]
            o, c, ps, last_k = data[t]
            if ps[k] >= 0:
                p["last"] = c[ps[k]]
                if k >= p["exit_k"]:
                    close_pos(t, p["last"], k)
            elif k > last_k:
                close_pos(t, p["last"], k)
        short_val = sum(-p["sh"] * p["last"] for p in pos.values() if p["side"] == -1)
        cash -= short_val * borrow / 252
        mv = sum(p["sh"] * p["last"] for p in pos.values())
        eq[k] = cash + mv
        gross[k] = sum(abs(p["sh"] * p["last"]) for p in pos.values()) / eq[k]
        net[k] = mv / eq[k]
    return eq, gross, net, trades, k0, k_end


def stats(eq, gross, net, trades, spy_close, dates, a, b, k0):
    ka = max(int(dates.searchsorted(a)), k0 + 1)
    kb = int(dates.searchsorted(b, side="right")) - 1
    r = eq[ka:kb + 1] / eq[ka - 1:kb] - 1
    s = spy_close[ka:kb + 1] / spy_close[ka - 1:kb] - 1
    bh, se = ols_hac(r, s)
    tr = np.array([x[4] for x in trades if ka <= x[3] <= kb])
    sides = np.array([x[1] for x in trades if ka <= x[3] <= kb])
    n = len(r)
    return dict(cagr=(eq[kb] / eq[ka - 1]) ** (252 / n) - 1, vol=r.std() * np.sqrt(252), sharpe=r.mean() / r.std() * np.sqrt(252),
                dd=(eq[ka - 1:kb + 1] / np.maximum.accumulate(eq[ka - 1:kb + 1]) - 1).min(), beta=bh[1], alpha=bh[0] * 252,
                t=bh[0] / se[0], gross=gross[ka:kb + 1].mean(), net=net[ka:kb + 1].mean(), n=len(tr),
                avg_long=tr[sides == 1].mean() if (sides == 1).any() else np.nan,
                avg_short=tr[sides == -1].mean() if (sides == -1).any() else np.nan,
                spy=(spy_close[kb] / spy_close[ka - 1]) ** (252 / n) - 1)


def line(label, m):
    return (f"{label:30}{m['cagr'] * 100:>+7.1f}%{m['vol'] * 100:>6.1f}%{m['sharpe']:>7.2f}{m['dd'] * 100:>7.1f}%{m['beta']:>+6.2f}"
            f"{m['alpha'] * 100:>+10.2f} ({m['t']:+.1f}){m['gross'] * 100:>6.0f}%{m['net'] * 100:>+6.0f}%{m['n']:>6}"
            f"{m['avg_long'] * 100:>+8.2f}%{m['avg_short'] * 100:>+8.2f}%")


def run(stage: str) -> None:
    panel, spy, dates, data = load()
    spy_close = spy["Close"].to_numpy()
    cands = build_candidates(panel)
    if stage == "design":
        a, b, end, title = START, DESIGN_END, DESIGN_END, "DISENO 2005-2020"
    else:
        a, b, end, title = TEST_START, pd.Timestamp("2100-01-01"), pd.Timestamp("2100-01-01"), "PRUEBA 2021-2026"
    head = f"{'':30}{'CAGR':>8}{'vol':>7}{'Sharpe':>7}{'maxDD':>8}{'beta':>6}{'alpha anual % (t)':>20}{'bruto':>6}{'neto':>7}{'ops':>6}{'largo/op':>9}{'corto/op':>9}"
    print(f"\n{title}\n{head}")
    out = {}
    for label, sides in (("PRINCIPAL largo+corto", (1, -1)), ("  solo pata larga", (1,)), ("  solo pata corta", (-1,))):
        e = simulate(cands, dates, data, BASE_COST, BASE_BORROW, end, sides)
        b_ = min(b, dates[e[5]])
        out[label] = stats(*e[:4], spy_close, dates, a, b_, e[4])
        print(line(label + " (10pb, prest. 0,5%)", out[label]))
    print(f"SPY en el periodo: CAGR {out['PRINCIPAL largo+corto']['spy'] * 100:+.1f}%")
    print("\nSensibilidad (largo+corto): alpha anual % (t)")
    print(f"{'coste RT / prestamo':>22}{'0,25 %':>16}{'0,5 %':>16}{'2 %':>16}")
    for cp in (0.0005, 0.0010, 0.0030):
        row = f"{cp * 1e4:>19.0f} pb"
        for bw in (0.0025, 0.005, 0.02):
            e = simulate(cands, dates, data, cp, bw, end)
            m = stats(*e[:4], spy_close, dates, a, min(b, dates[e[5]]), e[4])
            row += f"{m['alpha'] * 100:>+10.2f} ({m['t']:+.1f})"
        print(row)
    m = out["PRINCIPAL largo+corto"]
    if stage == "design":
        ok = m["alpha"] > 0 and m["t"] > 2
        print(f"\nCRITERIO 1 (alpha neto > 0 y t > 2 en diseno): {'CUMPLE -> se puede evaluar la prueba' if ok else 'NO CUMPLE -> hipotesis RECHAZADA, la ventana de prueba no se toca'}")


if __name__ == "__main__":
    run(sys.argv[1])
