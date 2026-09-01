"""
Walk-forward backtest — CongressTrader (ventana 180 días).

Metodología:
  - Cada 30 días, se rankea a los políticos usando SOLO trades con pub_date <= fecha actual
    (sin look-ahead bias: respeta el lag de publicación de Capitol Trades)
  - Se toman las posiciones abiertas del mejor político y se simulan durante el mes siguiente
  - Equal weight entre posiciones; sin costes de transacción ni slippage
  - Se ejecutan DOS pasadas: con todos los datos y excluyendo hiperoperadores (>100 trades/ventana)

Limitaciones documentadas (ver sección al final del output):
  - Solo 8 periodos mensuales (datos desde jun 2025)
  - Ventana de 180 días reduce el bias de muestra pequeña pero no lo elimina
  - Resultados estadísticamente débiles por el número de periodos
  - Estrategia puede depender de outliers (Khanna, McClain)

Uso:  python backtest.py
Output: backtest_results.csv, backtest_results_sin_hiperoperadores.csv
"""

import csv
import math
import pickle
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf
import pandas as pd

from analyzer import (
    _fetch_historical_prices_bulk,
    _lookup_close,
    _compute_score,
)
from config import MIN_TRADES, TOP_PERFORMERS, MAX_POSITIONS

CACHE_FILE    = Path(__file__).parent / "trades_cache.pkl"
RANKING_DAYS  = 180   # ventana de ranking reducida para maximizar periodos disponibles
STEP_DAYS     = 30    # paso del walk-forward
RISK_FREE_ANN = 0.05  # tasa libre de riesgo anual (~Fed funds 2025-2026)

HYPERTRADER_CAP = 100  # políticos con >N trades en la ventana se consideran hiperoperadores


def _build_spy_lookup(start: datetime, end: datetime) -> dict[str, float]:
    """Descarga precios de SPY via yfinance (más fiable que IEX para ETFs)."""
    print("[backtest] Obteniendo precios de SPY via yfinance…")
    ticker = yf.Ticker("SPY")
    df = ticker.history(
        start=(start - timedelta(days=5)).strftime("%Y-%m-%d"),
        end=(end   + timedelta(days=5)).strftime("%Y-%m-%d"),
        interval="1d",
    )
    if df.empty:
        print("[backtest] WARN: yfinance no devolvió datos de SPY.")
        return {}
    lookup: dict[str, float] = {}
    for ts, row in df.iterrows():
        date_str = str(ts)[:10]
        lookup[date_str] = float(row["Close"])
    print(f"[backtest] SPY: {len(lookup)} días cargados ({min(lookup)} → {max(lookup)})")
    return lookup


def _spy_price_at(spy_lookup: dict[str, float], date_str: str) -> float | None:
    """Busca el precio de SPY en date_str o el siguiente día hábil (hasta 7 días)."""
    target = datetime.fromisoformat(date_str).date()
    for offset in range(8):
        candidate = (target + timedelta(days=offset)).isoformat()
        price = spy_lookup.get(candidate)
        if price:
            return price
    return None


def _load_trades() -> list[dict]:
    if not CACHE_FILE.exists():
        raise FileNotFoundError("trades_cache.pkl no encontrado.")
    with open(CACHE_FILE, "rb") as f:
        data = pickle.load(f)
    # _load_trades devuelve la lista directamente (el pkl puede ser dict o list)
    return data["trades"] if isinstance(data, dict) else data


def _rank_at_date(
    trades: list[dict],
    as_of: datetime,
    hist_lookup: dict,
    max_trades_cap: int | None = None,
) -> list[dict]:
    """
    Point-in-time ranking usando solo pub_date <= as_of.
    max_trades_cap: si se indica, excluye políticos con más de N compras en la ventana
                    (análisis de robustez vs hiperoperadores).
    """
    cutoff = as_of - timedelta(days=RANKING_DAYS)

    buys_by_pol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t["tx_type"] != "buy":
            continue
        pub = t.get("pub_date") or t.get("tx_date", "")
        if not pub:
            continue
        pub_dt = datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc)
        if pub_dt > as_of or pub_dt < cutoff:
            continue
        buys_by_pol[t["pol_id"]].append(t)

    rankings = []
    as_of_str = as_of.strftime("%Y-%m-%d")

    for pol_id, buys in buys_by_pol.items():
        if len(buys) < MIN_TRADES:
            continue
        if max_trades_cap is not None and len(buys) > max_trades_cap:
            continue  # excluir hiperoperadores

        per_trade_returns, weighted_return, total_weight = [], 0.0, 0.0
        for trade in buys:
            ticker     = trade["ticker"]
            entry_date = (trade.get("pub_date") or trade["tx_date"])[:10]
            buy_price  = _lookup_close(hist_lookup, ticker, entry_date)
            cur_price  = _lookup_close(hist_lookup, ticker, as_of_str)
            if not buy_price or not cur_price or buy_price == 0:
                continue
            ret    = (cur_price - buy_price) / buy_price
            weight = trade.get("value", 8000)
            per_trade_returns.append(ret)
            weighted_return += ret * weight
            total_weight    += weight

        if not per_trade_returns or total_weight == 0:
            continue

        avg_return = weighted_return / total_weight
        win_rate   = sum(1 for r in per_trade_returns if r > 0) / len(per_trade_returns)
        n          = len(per_trade_returns)
        if n > 1:
            mean_r   = sum(per_trade_returns) / n
            variance = sum((r - mean_r) ** 2 for r in per_trade_returns) / (n - 1)
            std_r    = math.sqrt(variance) if variance > 0 else 0
            sharpe   = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        rankings.append({
            "pol_id":   pol_id,
            "pol_name": buys[0]["pol_name"],
            "n_trades": n,
            "score":    _compute_score(avg_return, win_rate, sharpe, n),
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings


def _open_positions_at(pol_id: str, trades: list[dict], as_of: datetime) -> list[str]:
    """Tickers con net buys > 0 a fecha as_of (usando pub_date)."""
    net: dict[str, float] = defaultdict(float)
    for t in trades:
        if t["pol_id"] != pol_id:
            continue
        pub = t.get("pub_date") or t.get("tx_date", "")
        if not pub:
            continue
        if datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc) > as_of:
            continue
        val = t.get("value", 8000)
        if t["tx_type"] == "buy":
            net[t["ticker"]] += val
        elif t["tx_type"] == "sell":
            net[t["ticker"]] -= val
    return [tkr for tkr, amt in net.items() if amt > 0][:MAX_POSITIONS]


def _compute_metrics(monthly_returns: list[float]) -> dict:
    """Calcula métricas estándar a partir de retornos mensuales."""
    if not monthly_returns:
        return {}

    n = len(monthly_returns)
    mean_m = sum(monthly_returns) / n
    rf_monthly = RISK_FREE_ANN / 12

    # Sharpe anualizado (exceso de retorno sobre tasa libre de riesgo)
    if n > 1:
        variance = sum((r - mean_m) ** 2 for r in monthly_returns) / (n - 1)
        std_m = math.sqrt(variance) if variance > 0 else 0
        sharpe_ann = ((mean_m - rf_monthly) / std_m * math.sqrt(12)) if std_m > 0 else 0.0
    else:
        std_m = 0.0
        sharpe_ann = 0.0

    # Retorno total y anualizado
    total_ret = 1.0
    for r in monthly_returns:
        total_ret *= (1 + r)
    total_ret -= 1
    ann_ret = (1 + total_ret) ** (12 / n) - 1 if n > 0 else 0.0

    # Max drawdown
    peak = 1.0
    equity = 1.0
    max_dd = 0.0
    for r in monthly_returns:
        equity *= (1 + r)
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak
        if dd < max_dd:
            max_dd = dd

    return {
        "n_periods":    n,
        "total_ret":    total_ret,
        "ann_ret":      ann_ret,
        "mean_monthly": mean_m,
        "std_monthly":  std_m,
        "sharpe_ann":   sharpe_ann,
        "max_drawdown": max_dd,
    }


def _run_backtest(
    trades: list[dict],
    hist_lookup: dict,
    spy_lookup: dict,
    data_start: datetime,
    data_end: datetime,
    max_trades_cap: int | None,
    label: str,
) -> tuple[list[dict], list[float], list[float]]:
    """Ejecuta el walk-forward y devuelve (rows, bot_monthly_rets, spy_monthly_rets)."""
    bt_start = data_start + timedelta(days=RANKING_DAYS)
    spy_baseline = _spy_price_at(spy_lookup, bt_start.strftime("%Y-%m-%d"))
    portfolio = 100_000.0
    results: list[dict] = []
    bot_monthly: list[float] = []
    spy_monthly_list: list[float] = []

    current = bt_start
    while current <= data_end - timedelta(days=STEP_DAYS):
        next_dt = current + timedelta(days=STEP_DAYS)

        rankings = _rank_at_date(trades, current, hist_lookup, max_trades_cap)
        if not rankings:
            current = next_dt
            continue

        leaders = rankings[:TOP_PERFORMERS]
        merged: dict[str, int] = {}
        for leader in leaders:
            for tkr in _open_positions_at(leader["pol_id"], trades, current):
                merged[tkr] = merged.get(tkr, 0) + 1

        if not merged:
            current = next_dt
            continue

        cur_str  = current.strftime("%Y-%m-%d")
        next_str = next_dt.strftime("%Y-%m-%d")

        pos_returns = []
        for tkr in merged:
            p0 = _lookup_close(hist_lookup, tkr, cur_str)
            p1 = _lookup_close(hist_lookup, tkr, next_str)
            if p0 and p1 and p0 != 0:
                pos_returns.append((p1 - p0) / p0)

        if not pos_returns:
            current = next_dt
            continue

        monthly_ret = sum(pos_returns) / len(pos_returns)
        portfolio  *= (1 + monthly_ret)
        bot_monthly.append(monthly_ret)

        spy0 = _spy_price_at(spy_lookup, cur_str)
        spy1 = _spy_price_at(spy_lookup, next_str)
        spy_ret = (spy1 - spy0) / spy0 if spy0 and spy1 else 0.0
        spy_monthly_list.append(spy_ret)

        spy_now = spy1 or 0.0
        spy_cum = (spy_now - spy_baseline) / spy_baseline if spy_baseline else 0.0
        bot_cum = (portfolio - 100_000) / 100_000

        results.append({
            "date":            cur_str,
            "top_performer":   leaders[0]["pol_name"],
            "n_positions":     len(merged),
            "n_trades_leader": leaders[0]["n_trades"],
            "portfolio_value": f"{portfolio:.2f}",
            "bot_cumulative":  f"{bot_cum:.4f}",
            "monthly_return":  f"{monthly_ret:.4f}",
            "spy_price":       f"{spy_now:.2f}",
            "spy_cumulative":  f"{spy_cum:.4f}",
            "spy_monthly":     f"{spy_ret:.4f}",
            "bot_vs_spy":      f"{bot_cum - spy_cum:.4f}",
        })

        cap_info = f"  [cap={max_trades_cap}]" if max_trades_cap else ""
        print(
            f"  {cur_str}  ${portfolio:>10,.0f} ({monthly_ret:+.2%})  "
            f"SPY {spy_ret:+.2%}  → {leaders[0]['pol_name']} ({leaders[0]['n_trades']} trades){cap_info}"
        )
        current = next_dt

    return results, bot_monthly, spy_monthly_list


def _print_metrics(label: str, bot: dict, spy: dict) -> None:
    print(f"\n{'─'*55}")
    print(f"  {label}")
    print(f"{'─'*55}")
    print(f"  Periodos simulados:    {bot['n_periods']}")
    print(f"  Retorno total bot:     {bot['total_ret']:+.2%}")
    print(f"  Retorno total SPY:     {spy['total_ret']:+.2%}")
    print(f"  Bot vs SPY (total):    {bot['total_ret']-spy['total_ret']:+.2%}")
    print(f"  Retorno anualizado bot:{bot['ann_ret']:+.2%}")
    print(f"  Retorno anualizado SPY:{spy['ann_ret']:+.2%}")
    print(f"  Sharpe anualizado bot: {bot['sharpe_ann']:+.2f}")
    print(f"  Sharpe anualizado SPY: {spy['sharpe_ann']:+.2f}")
    print(f"  Max drawdown bot:      {bot['max_drawdown']:.2%}")
    print(f"  Max drawdown SPY:      {spy['max_drawdown']:.2%}")


def _print_limitations() -> None:
    print(f"\n{'='*55}")
    print("  LIMITACIONES — leer antes de interpretar resultados")
    print(f"{'='*55}")
    limitations = [
        ("Pocos periodos",
         f"Solo {8} periodos mensuales (datos desde jun 2025). "
         "Con tan pocos datos, cualquier resultado puede deberse al azar. "
         "Se necesitan ≥36 periodos para obtener significancia estadística básica."),
        ("Look-ahead bias: corregido",
         "Se usa pub_date (fecha de publicación en Capitol Trades) como señal de entrada, "
         "NO tx_date (fecha real de la operación). Esto respeta el lag de declaración "
         "de la ley STOCK Act (hasta 45 días)."),
        ("Sesgo de volumen",
         "El score pondera por valor declarado de la operación. "
         "Congresistas con muchas operaciones pequeñas (Khanna: 449 trades/90d) "
         "pueden dominar el ranking sin necesariamente ser mejores inversores."),
        ("Sin costes de transacción",
         "La simulación no incluye comisiones, slippage ni spread bid-ask. "
         "En carteras de pequeño tamaño esto puede ser significativo."),
        ("Datos de Capitol Trades solo desde jun 2025",
         "La web de Capitol Trades solo indexa disclosures recientes (~14 meses). "
         "Para un backtest riguroso de 3-5 años se necesitaría Quiver Quantitative "
         "u otra fuente con histórico completo desde 2019."),
        ("Survivorship bias parcial",
         "Se incluyen todos los tickers en el cache actual. "
         "Empresas que quebraron o fueron adquiridas antes de jun 2025 "
         "no aparecen en el histórico, lo que puede sobreestimar levemente los retornos."),
    ]
    for title, desc in limitations:
        print(f"\n  [{title}]")
        # Wrap text at ~70 chars
        words = desc.split()
        line = "    "
        for word in words:
            if len(line) + len(word) + 1 > 72:
                print(line)
                line = "    " + word + " "
            else:
                line += word + " "
        if line.strip():
            print(line)
    print(f"\n{'='*55}")


def run() -> None:
    print("[backtest] Cargando trades del cache…")
    trades = _load_trades()
    print(f"[backtest] {len(trades)} trades cargados.")

    pub_dates = [
        datetime.fromisoformat(t["pub_date"][:10]).replace(tzinfo=timezone.utc)
        for t in trades if t.get("pub_date")
    ]
    if not pub_dates:
        print("[backtest] Sin trades con pub_date.")
        return

    data_start = min(pub_dates)
    data_end   = max(pub_dates)
    print(f"[backtest] Rango de datos: {data_start.date()} → {data_end.date()}")
    print(f"[backtest] Ventana ranking: {RANKING_DAYS}d | Paso: {STEP_DAYS}d")

    bt_start = data_start + timedelta(days=RANKING_DAYS)
    if bt_start >= data_end - timedelta(days=STEP_DAYS):
        print("[backtest] Datos insuficientes para walk-forward.")
        return

    # Fetch precios históricos del portfolio (Alpaca IEX)
    all_tickers = list({t["ticker"] for t in trades})
    print(f"[backtest] Obteniendo precios históricos para {len(all_tickers)} tickers…")
    hist_lookup = _fetch_historical_prices_bulk(
        all_tickers,
        data_start - timedelta(days=7),
        data_end   + timedelta(days=7),
    )
    print(f"[backtest] {len(hist_lookup)} puntos de precio obtenidos.")

    # SPY via yfinance (más fiable que IEX para ETFs de NYSE Arca)
    spy_lookup = _build_spy_lookup(data_start, data_end)
    print()

    # ── PASADA 1: todos los políticos elegibles ────────────────────────────────
    print("═" * 55)
    print("  PASADA 1 — Todos los políticos (sin filtro)")
    print("═" * 55)
    results_full, bot_m_full, spy_m_full = _run_backtest(
        trades, hist_lookup, spy_lookup, data_start, data_end,
        max_trades_cap=None, label="full",
    )

    # ── PASADA 2: excluyendo hiperoperadores (>100 trades en ventana) ──────────
    print(f"\n{'═'*55}")
    print(f"  PASADA 2 — Sin hiperoperadores (cap={HYPERTRADER_CAP} trades/ventana)")
    print(f"{'═'*55}")
    results_cap, bot_m_cap, spy_m_cap = _run_backtest(
        trades, hist_lookup, spy_lookup, data_start, data_end,
        max_trades_cap=HYPERTRADER_CAP, label="cap",
    )

    # ── Métricas ───────────────────────────────────────────────────────────────
    m_bot_full = _compute_metrics(bot_m_full)
    m_spy_full = _compute_metrics(spy_m_full)
    m_bot_cap  = _compute_metrics(bot_m_cap)
    m_spy_cap  = _compute_metrics(spy_m_cap)

    print("\n")
    _print_metrics("Pasada 1 — TODOS los políticos", m_bot_full, m_spy_full)
    _print_metrics(f"Pasada 2 — SIN hiperoperadores (>{HYPERTRADER_CAP} trades)", m_bot_cap, m_spy_cap)

    # ── Comparación de robustez ────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print("  ANÁLISIS DE ROBUSTEZ")
    print(f"{'─'*55}")
    diff_ret = m_bot_full.get("total_ret", 0) - m_bot_cap.get("total_ret", 0)
    diff_shr = m_bot_full.get("sharpe_ann", 0) - m_bot_cap.get("sharpe_ann", 0)
    if abs(diff_ret) < 0.05:
        verdict = "ROBUSTO — los hiperoperadores apenas afectan al resultado"
    elif diff_ret > 0:
        verdict = "DEPENDIENTE — el resultado mejora CON hiperoperadores (strategy driven by outliers)"
    else:
        verdict = "INVERSO — el resultado mejora SIN hiperoperadores"
    print(f"  Diferencia retorno total (full vs cap): {diff_ret:+.2%}")
    print(f"  Diferencia Sharpe anualizado:           {diff_shr:+.2f}")
    print(f"  Veredicto: {verdict}")

    # ── Guardar CSVs ───────────────────────────────────────────────────────────
    if results_full:
        out = Path(__file__).parent / "backtest_results.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results_full[0].keys()))
            writer.writeheader()
            writer.writerows(results_full)
        print(f"\n[backtest] Guardado: {out.name}")

    if results_cap:
        out_cap = Path(__file__).parent / "backtest_results_sin_hiperoperadores.csv"
        with open(out_cap, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results_cap[0].keys()))
            writer.writeheader()
            writer.writerows(results_cap)
        print(f"[backtest] Guardado: {out_cap.name}")

    # ── Limitaciones ───────────────────────────────────────────────────────────
    _print_limitations()


if __name__ == "__main__":
    run()
