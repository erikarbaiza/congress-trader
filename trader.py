"""
Mirrors a politician's positions in the Alpaca account with full risk controls:
  - Paper trading by default (REAL_TRADING=true required for live)
  - Max 5% of portfolio per position
  - Max 20% per sector
  - Max 10 open positions
  - No penny stocks (< MIN_PRICE)
  - No low-volume stocks (< MIN_VOLUME avg daily)
  - Stop-loss and take-profit on existing positions
"""

from collections import defaultdict

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from datetime import datetime, timedelta, timezone

from config import (
    ALPACA_KEY, ALPACA_SECRET, PAPER_TRADING, DEPLOY_BUDGET,
    MAX_POSITIONS, MAX_POSITION_PCT, MAX_SECTOR_PCT,
    MIN_PRICE, MIN_VOLUME, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
)

_trading_client = None
_data_client = None


def _get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER_TRADING)
    return _trading_client


def _get_data_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
    return _data_client


def get_account_equity() -> float:
    return float(_get_trading_client().get_account().equity)


def get_current_positions() -> dict[str, dict]:
    """Return {ticker: {market_value, avg_entry_price, current_price, qty}}."""
    positions = _get_trading_client().get_all_positions()
    return {
        p.symbol: {
            "market_value":    float(p.market_value),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price":   float(p.current_price),
            "qty":             float(p.qty),
        }
        for p in positions
    }


def _get_quote(ticker: str) -> dict | None:
    """Return {price, volume} for a ticker using latest quote + recent bars."""
    try:
        # Latest price via quote
        quotes = _get_data_client().get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=ticker)
        )
        q = quotes.get(ticker)
        price = float(q.ask_price or q.bid_price) if q else None

        # Average volume from last 20 trading days
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        bars = _get_data_client().get_stock_bars(
            StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day,
                             start=start, end=end, feed=DataFeed.IEX)
        )
        df = bars.df
        avg_vol = float(df["volume"].mean()) if not df.empty else 0

        return {"price": price, "avg_volume": avg_vol}
    except Exception:
        return None


def _passes_risk_filters(ticker: str, quote: dict, log: list) -> bool:
    """Return False (and log reason) if the stock fails any pre-trade filter."""
    price = quote.get("price") or 0
    vol = quote.get("avg_volume") or 0

    if price < MIN_PRICE:
        log.append({"action": "SKIP", "ticker": ticker, "qty": None,
                    "reason": f"penny stock (${price:.2f} < ${MIN_PRICE})"})
        return False

    if MIN_VOLUME > 0 and vol < MIN_VOLUME:
        log.append({"action": "SKIP", "ticker": ticker, "qty": None,
                    "reason": f"low volume ({vol:,.0f} < {MIN_VOLUME:,})"})
        return False

    return True


def _check_stop_take(current_positions: dict, log: list) -> None:
    """Close any position that has hit its stop-loss or take-profit."""
    client = _get_trading_client()
    for ticker, pos in current_positions.items():
        entry = pos["avg_entry_price"]
        price = pos["current_price"]
        if entry == 0:
            continue
        change = (price - entry) / entry

        if STOP_LOSS_PCT > 0 and change <= -STOP_LOSS_PCT:
            try:
                client.close_position(ticker)
                log.append({"action": "STOP_LOSS", "ticker": ticker, "qty": None,
                            "reason": f"down {change:.1%} from entry ${entry:.2f}"})
            except Exception as e:
                log.append({"action": "ERROR_STOP", "ticker": ticker, "qty": None,
                            "reason": str(e)})

        elif TAKE_PROFIT_PCT > 0 and change >= TAKE_PROFIT_PCT:
            try:
                client.close_position(ticker)
                log.append({"action": "TAKE_PROFIT", "ticker": ticker, "qty": None,
                            "reason": f"up {change:.1%} from entry ${entry:.2f}"})
            except Exception as e:
                log.append({"action": "ERROR_TP", "ticker": ticker, "qty": None,
                            "reason": str(e)})


def mirror_positions(target: list[dict]) -> list[dict]:
    """
    Align the account with `target` (list of {ticker, net_dollars, sector}).
    Applies all risk controls before placing any order.
    Returns a log of every action taken.
    """
    if not PAPER_TRADING:
        print("[trader] LIVE TRADING MODE — real money.")

    client = _get_trading_client()
    equity = float(client.get_account().equity)
    budget = DEPLOY_BUDGET if DEPLOY_BUDGET > 0 else equity

    current = get_current_positions()
    log: list[dict] = []

    # ── 0. Check stop-loss / take-profit on existing positions ────────────────
    _check_stop_take(current, log)
    # Refresh after potential closes
    current = get_current_positions()

    target_tickers = {p["ticker"] for p in target}

    # ── 1. Close positions no longer in target ────────────────────────────────
    for ticker in list(current.keys()):
        if ticker not in target_tickers:
            try:
                client.close_position(ticker)
                log.append({"action": "CLOSE", "ticker": ticker, "qty": None,
                            "reason": "not in top performer's portfolio"})
            except Exception as e:
                log.append({"action": "ERROR_CLOSE", "ticker": ticker, "qty": None,
                            "reason": str(e)})

    if not target:
        return log

    # Equal weight: each position gets 1/N of the budget.
    # Weighting by congressional notional distorts results — one $4M trade
    # would dominate and leave 94% of the budget idle in cash.
    equal_weight = 1.0 / len(target)

    # ── 3. Place / adjust positions ───────────────────────────────────────────
    positions_open = len([t for t in target_tickers if t in current])
    sector_spent: dict[str, float] = defaultdict(float)

    for pos in target:
        ticker = pos["ticker"]
        sector = pos["sector"]

        # Hard cap: max MAX_POSITIONS open
        if ticker not in current and positions_open >= MAX_POSITIONS:
            log.append({"action": "SKIP", "ticker": ticker, "qty": None,
                        "reason": f"max {MAX_POSITIONS} positions reached"})
            continue

        # Position size cap
        raw_value = budget * equal_weight
        capped_value = min(raw_value, budget * MAX_POSITION_PCT)

        # Sector cap
        if sector_spent[sector] + capped_value > budget * MAX_SECTOR_PCT:
            remaining = budget * MAX_SECTOR_PCT - sector_spent[sector]
            if remaining <= 0:
                log.append({"action": "SKIP", "ticker": ticker, "qty": None,
                            "reason": f"sector {sector} at {MAX_SECTOR_PCT:.0%} cap"})
                continue
            capped_value = remaining

        # Fetch live quote and validate
        quote = _get_quote(ticker)
        if not quote or not quote.get("price"):
            log.append({"action": "SKIP", "ticker": ticker, "qty": None,
                        "reason": "could not fetch quote"})
            continue

        if not _passes_risk_filters(ticker, quote, log):
            continue

        price = quote["price"]
        target_qty = int(capped_value / price)
        if target_qty < 1:
            continue

        current_qty = int(current.get(ticker, {}).get("qty", 0))
        diff_qty = target_qty - current_qty

        if diff_qty == 0:
            log.append({"action": "HOLD", "ticker": ticker, "qty": current_qty,
                        "reason": "already at target size"})
            sector_spent[sector] += current_qty * price
            continue

        side = OrderSide.BUY if diff_qty > 0 else OrderSide.SELL
        qty = abs(diff_qty)

        try:
            client.submit_order(MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            ))
            log.append({"action": side.value.upper(), "ticker": ticker, "qty": qty,
                        "reason": f"target {target_qty} shares @ ~${price:.2f}"})
            if side == OrderSide.BUY:
                sector_spent[sector] += qty * price
                if ticker not in current:
                    positions_open += 1
        except Exception as e:
            log.append({"action": "ERROR_ORDER", "ticker": ticker, "qty": qty,
                        "reason": str(e)})

    return log
