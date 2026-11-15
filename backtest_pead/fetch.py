"""
Fase 1b: descarga reanudable de sorpresas de BPA y precios diarios para todo el universo.

  - Sorpresas: yfinance get_earnings_dates(limit=100)            -> cache/events/<TICKER>.pkl
  - Precios:   yfinance (ajustados, desde 2004)                   -> cache/prices/<TICKER>.pkl
  - Respaldo:  Alpaca SIP diario (ajustado, desde 2016) para empresas que ya no existen en yfinance
Se puede parar y relanzar: lo ya descargado se salta.

Uso:  python backtest_pead/fetch.py [events|prices|alpaca|all]
"""
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from universe import CACHE, load_universe   # noqa: E402

warnings.filterwarnings("ignore")
EV_DIR, PX_DIR = CACHE / "events", CACHE / "prices"
PX_START = "2004-01-01"
ALPACA_START = "2016-01-01"
COLS = ["Open", "High", "Low", "Close", "Volume"]


def _tickers(u: pd.DataFrame) -> list[str]:
    return sorted(u["yf_ticker"].unique())


def fetch_events(tickers: list[str], retry: set[str]) -> None:
    EV_DIR.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tickers if not (EV_DIR / f"{t}.pkl").exists()]
    print(f"[events] {len(todo)} por descargar de {len(tickers)}", flush=True)

    def one(t: str) -> None:
        # yfinance traga los errores de limite de peticiones y devuelve vacio: un vacio solo se acepta
        # como "sin datos" tras reintentar con pausa creciente. Las empresas que salieron del indice
        # antes de 2016 casi nunca tienen datos: un solo intento.
        ed = pd.DataFrame()
        for attempt in range(3 if t in retry else 1):
            try:
                r = yf.Ticker(t).get_earnings_dates(limit=100)
                if r is not None and not r.empty:
                    ed = r.dropna(subset=["Reported EPS"]).sort_index()
                    if len(ed):
                        break
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        ed.to_pickle(EV_DIR / f"{t}.pkl")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, _ in enumerate(pool.map(one, todo), 1):
            if i % 50 == 0:
                print(f"[events] {i}/{len(todo)}", flush=True)


def fetch_prices_yf(tickers: list[str], chunk: int = 40) -> None:
    PX_DIR.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tickers if not (PX_DIR / f"{t}.pkl").exists()]
    print(f"[prices] {len(todo)} por descargar de {len(tickers)}", flush=True)
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        batch = part if len(part) > 1 else part + ["SPY"]
        try:
            raw = yf.download(batch, start=PX_START, auto_adjust=True, group_by="ticker",
                              progress=False, threads=True)
        except Exception:
            raw = None
        for t in part:
            try:
                df = raw[t][COLS].dropna(subset=["Close"]) if raw is not None else pd.DataFrame()
            except Exception:
                df = pd.DataFrame()
            df.to_pickle(PX_DIR / f"{t}.pkl")
        print(f"[prices] {min(i + chunk, len(todo))}/{len(todo)}", flush=True)


def fetch_prices_alpaca(u: pd.DataFrame, chunk: int = 40) -> None:
    """Respaldo para empresas sin precios en yfinance que estuvieron en el indice desde 2016."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from config import ALPACA_KEY, ALPACA_SECRET

    def empty(t: str) -> bool:
        p = PX_DIR / f"{t}.pkl"
        return (not p.exists()) or pd.read_pickle(p).empty

    recent = u[u["end"].isna() | (u["end"] >= pd.Timestamp(ALPACA_START))]
    todo = sorted({r.ticker for r in recent.itertuples() if empty(r.yf_ticker)})
    print(f"[alpaca] {len(todo)} empresas sin precios en yfinance (activas desde 2016)", flush=True)
    client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        try:
            bars = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=part, timeframe=TimeFrame.Day, adjustment=Adjustment.ALL,
                start=pd.Timestamp(ALPACA_START, tz="UTC"), feed=DataFeed.SIP)).df
        except Exception as e:
            print(f"[alpaca] lote {i} fallo: {str(e)[:80]}", flush=True)
            continue
        if bars.empty:
            continue
        for sym, g in bars.groupby(level=0):
            df = g.droplevel(0)
            df.index = pd.DatetimeIndex(df.index).tz_convert(None).normalize()
            df = df.rename(columns=str.capitalize)[COLS]
            df.to_pickle(PX_DIR / f"{sym.replace('.', '-')}.pkl")
        print(f"[alpaca] {min(i + chunk, len(todo))}/{len(todo)}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    uni = load_universe()
    tks = _tickers(uni)
    if what in ("events", "all"):
        recent = uni[uni["end"].isna() | (uni["end"] >= pd.Timestamp(ALPACA_START))]
        fetch_events(tks, retry=set(recent["yf_ticker"]))
    if what in ("prices", "all"):
        fetch_prices_yf(tks + ["SPY"])
    if what in ("alpaca", "all"):
        fetch_prices_alpaca(uni)
    print("[fetch] terminado", flush=True)
