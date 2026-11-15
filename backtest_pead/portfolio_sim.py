"""
Fase 3: simulacion de cartera del PEAD con las reglas del bot y 6 variantes preespecificadas.

Reglas comunes (como main.py/config.py): max 12 posiciones, sin apalancamiento (solo efectivo), compra en la apertura
del dia R+1, candidatos ordenados por sorpresa desc, stop-loss del 8 % evaluado al cierre y ejecutado en la apertura
siguiente. Salida por tiempo al cierre del dia (entrada + H dias habiles). Efectivo sin rendimiento.
NO modelado (sin datos historicos): tope sectorial del 35 %, tope de small caps del 8 %, filtro de precio/volumen.
Costes: round-trip de 10/30/100 pb (la mitad al entrar y la mitad al salir). Principal: 30 pb.

Variantes (6, fijadas antes de ver resultados):
  V0 base            regla del bot (>=5 %, |est|>=0,10 $), H=7 dias habiles (~10 naturales), tamano por sorpresa, stop 8 %
  V1 tamano igual    V0 con 1/12 por posicion
  V2 H=20            V0 con salida a 20 dias habiles
  V3 quintil alto    sorpresa/precio >= percentil 80 de los 4 anios previos (sin mirar el futuro) y sorpresa >= 5 %, H=7
  V4 quintil + H=20  V3 con H=20
  V5 sin stop        V0 sin stop-loss

PARTICION: DISENO = 2005-2020 (la simulacion de la etapa 'design' ni siquiera llega a 2021); PRUEBA = 2021-2026.
CRITERIO DE ELECCION (fijado antes): entre V1..V5 se elige la de mayor alpha anual neto a 30 pb en DISENO, siempre que su
t (Newey-West) sea > 2 y su drawdown maximo no sea >5 pp peor que el de V0. Si ninguna cumple, no se elige ninguna y en
PRUEBA solo se evalua V0.

Uso:
  python backtest_pead/portfolio_sim.py design
  python backtest_pead/portfolio_sim.py test V0,V3
"""
import sys

import numpy as np
import pandas as pd

from universe import CACHE

sys.stdout.reconfigure(encoding="utf-8")

MAX_POS, MIN_PCT, MAX_PCT, MIN_SURP, SCALE_SURP, STOP = 12, 0.05, 0.20, 0.05, 0.30, 0.08
COSTS = (0.0010, 0.0030, 0.0100)
PRIMARY = 0.0030
DESIGN_END, TEST_START = pd.Timestamp("2020-12-31"), pd.Timestamp("2021-01-01")
START = pd.Timestamp("2005-01-03")

VARIANTS = {
    "V0": dict(name="base (regla del bot)", H=7, stop=STOP, sizing="surprise", rule="bot"),
    "V1": dict(name="tamano igual", H=7, stop=STOP, sizing="equal", rule="bot"),
    "V2": dict(name="H=20", H=20, stop=STOP, sizing="surprise", rule="bot"),
    "V3": dict(name="quintil alto", H=7, stop=STOP, sizing="surprise", rule="top"),
    "V4": dict(name="quintil alto + H=20", H=20, stop=STOP, sizing="surprise", rule="top"),
    "V5": dict(name="sin stop", H=7, stop=None, sizing="surprise", rule="bot"),
}


# ── datos ────────────────────────────────────────────────────────────────────
def load():
    panel = pd.read_pickle(CACHE / "events_panel.pkl").dropna(subset=["entry_l1"]).copy()
    spy = pd.read_pickle(CACHE / "prices" / "SPY.pkl")
    spy = spy[~spy.index.duplicated(keep="last")].sort_index()
    dates = pd.DatetimeIndex(spy.index)
    data = {}
    for t in panel.ticker.unique():
        df = pd.read_pickle(CACHE / "prices" / f"{t}.pkl")
        df = df[~df.index.duplicated(keep="last")].sort_index().dropna(subset=["Open", "Close"])
        df.index = pd.DatetimeIndex(df.index)
        pos = df.index.get_indexer(dates)
        data[t] = (df["Open"].to_numpy(), df["Close"].to_numpy(), pos, int(np.where(pos >= 0)[0].max()))
    return panel, spy, dates, data


def rolling_p80(panel: pd.DataFrame) -> pd.Series:
    """Percentil 80 de surprise_px de los eventos de los 4 anios previos (estrictamente anteriores a cada evento)."""
    d = panel.sort_values("date")
    dts, vals = d["date"].to_numpy(), d["surprise_px"].to_numpy()
    out = {}
    for day in pd.unique(d["date"]):
        lo = np.searchsorted(dts, day - np.timedelta64(4 * 365, "D"), side="left")
        hi = np.searchsorted(dts, day, side="left")
        w = vals[lo:hi]
        w = w[~np.isnan(w)]
        out[day] = np.percentile(w, 80) if len(w) >= 300 else np.nan
    return d["date"].map(out).reindex(panel.index)


def candidates(panel: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "bot":
        m = (panel.surprise_pct >= MIN_SURP * 100) & (panel.est_abs >= 0.10)
    else:
        m = (panel.surprise_px >= panel["p80"]) & (panel.surprise_pct >= MIN_SURP * 100)
    return panel[m]


# ── simulacion ───────────────────────────────────────────────────────────────
def size_pct(surprise_pct: float, sizing: str) -> float:
    if sizing == "equal":
        return 1 / MAX_POS
    t = min(1.0, max(0.0, (surprise_pct / 100 - MIN_SURP) / (SCALE_SURP - MIN_SURP)))
    return MIN_PCT + t * (MAX_PCT - MIN_PCT)


def simulate(cands: pd.DataFrame, dates, data, H: int, stop, sizing: str, cost: float, end: pd.Timestamp):
    k0 = int(dates.searchsorted(START))
    k_end = int(dates.searchsorted(end, side="right")) - 1
    by_k: dict[int, list] = {}
    for r in cands.itertuples():
        k = dates.get_indexer([r.entry_l1])[0]
        if k >= k0:
            by_k.setdefault(k, []).append((r.surprise_pct, r.ticker))
    cash, pos = 100_000.0, {}
    eq = np.full(len(dates), np.nan)
    invested = np.zeros(len(dates))
    trades = []

    def sell(t, price, k):
        nonlocal cash
        p = pos.pop(t)
        proceeds = p["sh"] * price
        cash += proceeds * (1 - cost / 2)
        trades.append((t, p["k"], k, proceeds * (1 - cost / 2) / (p["val"] * (1 + cost / 2)) - 1))

    for k in range(k0, k_end + 1):
        # apertura: stops marcados ayer
        for t in [t for t, p in pos.items() if p["flag"]]:
            o, c, ps, _ = data[t]
            if ps[k] >= 0:
                sell(t, o[ps[k]], k)
        eq_prev = cash + sum(p["sh"] * p["last"] for p in pos.values())
        for sp, t in sorted(by_k.get(k, []), reverse=True):
            if len(pos) >= MAX_POS:
                break
            if t in pos:
                continue
            o, c, ps, _ = data[t]
            if ps[k] < 0:
                continue
            val = min(eq_prev * size_pct(sp, sizing), cash / (1 + cost / 2))
            if val < 100:
                continue
            price = o[ps[k]]
            cash -= val * (1 + cost / 2)
            pos[t] = dict(sh=val / price, ep=price, val=val, k=k, exit_k=k + H, last=price, flag=False)
        # cierre
        for t in list(pos):
            p = pos[t]
            o, c, ps, last_k = data[t]
            if ps[k] >= 0:
                p["last"] = c[ps[k]]
                if k >= p["exit_k"]:
                    sell(t, p["last"], k)
                elif stop is not None and p["last"] / p["ep"] - 1 <= -stop:
                    p["flag"] = True
            elif k > last_k:                       # la empresa dejo de cotizar: salida al ultimo cierre
                sell(t, p["last"], k)
        mv = sum(p["sh"] * p["last"] for p in pos.values())
        eq[k] = cash + mv
        invested[k] = mv / eq[k]
    return eq, invested, trades, k0, k_end


# ── metricas ─────────────────────────────────────────────────────────────────
def ols_hac(y, x, lag=5):
    X = np.column_stack([np.ones(len(x)), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ y
    xu = X * (y - X @ b)[:, None]
    S = xu.T @ xu
    for l in range(1, lag + 1):
        g = xu[l:].T @ xu[:-l]
        S += (1 - l / (lag + 1)) * (g + g.T)
    return b, np.sqrt(np.diag(xtx_inv @ S @ xtx_inv))


def metrics(eq, invested, trades, spy_close, dates, a: pd.Timestamp, b: pd.Timestamp, k0: int):
    ka = max(int(dates.searchsorted(a)), k0 + 1)
    kb = int(dates.searchsorted(b, side="right")) - 1
    r = eq[ka:kb + 1] / eq[ka - 1:kb] - 1
    s = spy_close[ka:kb + 1] / spy_close[ka - 1:kb] - 1
    n = len(r)
    bh, se = ols_hac(r, s)
    tr = [x[3] for x in trades if ka <= x[2] <= kb]
    dd = (eq[ka - 1:kb + 1] / np.maximum.accumulate(eq[ka - 1:kb + 1]) - 1).min()
    return dict(cagr=(eq[kb] / eq[ka - 1]) ** (252 / n) - 1, vol=r.std() * np.sqrt(252), sharpe=r.mean() / r.std() * np.sqrt(252),
                dd=dd, beta=bh[1], alpha=bh[0] * 252, t=bh[0] / se[0], expo=invested[ka:kb + 1].mean(),
                n=len(tr), avg=np.mean(tr) if tr else np.nan, win=np.mean(np.array(tr) > 0) if tr else np.nan,
                spy_cagr=(spy_close[kb] / spy_close[ka - 1]) ** (252 / n) - 1, spy_dd=(spy_close[ka - 1:kb + 1] / np.maximum.accumulate(spy_close[ka - 1:kb + 1]) - 1).min())


def run_stage(stage: str, keys: list[str]) -> dict:
    panel, spy, dates, data = load()
    panel["p80"] = rolling_p80(panel)
    spy_close = spy["Close"].to_numpy()
    if stage == "design":
        a, b, end = START, DESIGN_END, DESIGN_END
    else:
        a, b, end = TEST_START, pd.Timestamp("2100-01-01"), pd.Timestamp("2100-01-01")
    res = {}
    for key in keys:
        v = VARIANTS[key]
        c = candidates(panel, v["rule"])
        for cost in COSTS:
            eq, inv, tr, k0, kend = simulate(c, dates, data, v["H"], v["stop"], v["sizing"], cost, end)
            res[(key, cost)] = metrics(eq, inv, tr, spy_close, dates, a, min(b, dates[kend]), k0)
    return res


def show(res: dict, keys: list[str], title: str) -> None:
    ref = res[(keys[0], PRIMARY)]
    print(f"\n{title}\nSPY en el periodo: CAGR {ref['spy_cagr'] * 100:+.1f}% | max DD {ref['spy_dd'] * 100:.1f}%")
    print(f"{'variante':24}{'CAGR':>7}{'vol':>6}{'Sharpe':>7}{'maxDD':>7}{'beta':>6}{'expo':>6}{'alpha anual % (t) a 30pb':>26}{'a 10pb':>9}{'a 100pb':>9}{'ops':>6}{'neto/op':>9}{'acierto':>8}")
    for k in keys:
        m = res[(k, PRIMARY)]
        print(f"{k + ' ' + VARIANTS[k]['name']:24}{m['cagr'] * 100:>+6.1f}%{m['vol'] * 100:>5.1f}%{m['sharpe']:>7.2f}{m['dd'] * 100:>6.1f}%{m['beta']:>6.2f}{m['expo'] * 100:>5.0f}%"
              f"{m['alpha'] * 100:>+17.2f} ({m['t']:+.1f}){res[(k, COSTS[0])]['alpha'] * 100:>+11.2f}{res[(k, COSTS[2])]['alpha'] * 100:>+9.2f}"
              f"{m['n']:>6}{m['avg'] * 100:>+8.2f}%{m['win'] * 100:>7.0f}%")


if __name__ == "__main__":
    stage = sys.argv[1]
    if stage == "design":
        keys = list(VARIANTS)
        res = run_stage("design", keys)
        show(res, keys, "DISENO 2005-2020 (la simulacion no llega a 2021)")
        v0 = res[("V0", PRIMARY)]
        ok = [k for k in keys[1:] if res[(k, PRIMARY)]["t"] > 2 and res[(k, PRIMARY)]["dd"] >= v0["dd"] - 0.05]
        best = max(ok, key=lambda k: res[(k, PRIMARY)]["alpha"]) if ok else None
        print(f"\nCriterio preregistrado -> variante elegida: {best or 'NINGUNA (en PRUEBA solo V0)'}")
        print(f"Siguiente paso: python backtest_pead/portfolio_sim.py test V0{',' + best if best else ''}")
    else:
        keys = sys.argv[2].split(",")
        res = run_stage("test", keys)
        show(res, keys, "PRUEBA 2021-2026 (solo las variantes decididas en DISENO)")
