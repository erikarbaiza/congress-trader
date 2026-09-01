# CongressTrader

A quantitative system that ranks U.S. Congress members by historical stock performance and automatically replicates the top-ranked portfolio in Alpaca paper trading. Includes a walk-forward backtest with deliberate look-ahead bias correction.

> **Honest upfront:** the 8-period backtest shows the bot returning **−13.3%** while SPY returned **+11.4%** over the same period. This README explains why that result is valid, what it means, and what it cannot tell us.

---

## What It Does

Congress members in the U.S. must disclose stock trades within 45 days of execution (STOCK Act, 2012). The premise of this system — shared with a body of academic literature — is that some members may trade on non-public information. This bot:

1. Scrapes disclosures from [Capitol Trades](https://www.capitoltrades.com)
2. Ranks each member using a composite score over the trailing 180-day window
3. Replicates the top-ranked member's open positions in an Alpaca paper account
4. Runs daily via GitHub Actions at 15:35 UTC (market hours), automatically persisting state

The strategy was later extended to a second mode — **Earnings Momentum (PEAD)** — which buys S&P 500 stocks shortly after a positive EPS surprise and holds for 10 calendar days. The congressional replication is the focus of the backtest.

---

## Architecture

```
scraper.py          ← Capitol Trades HTML → structured trade records (pub_date-gated)
analyzer.py         ← composite score + open position estimation
trader.py           ← Alpaca order execution (paper by default)
backtest.py         ← walk-forward simulation with point-in-time correctness
earnings_scanner.py ← yfinance EPS surprise scanner for PEAD strategy
position_tracker.py ← persistent entry-date tracking for PEAD hold windows
main.py             ← daily orchestration (PEAD active; congressional in analyzer.py)
emailer.py          ← Resend HTTP API daily summary
logger.py           ← CSV equity curve for charting
```

**Infrastructure:** GitHub Actions (free tier, runs daily on cron). Secrets stored in repo settings. State files (`position_entries.json`, `sectors_cache.json`, `results_earnings.csv`) committed back to the repo after each run.

---

## Scoring Methodology

Each Congress member is ranked using a composite score over a trailing **180-day window** of buy disclosures:

```
score = 0.40 × return_score
      + 0.30 × win_rate
      + 0.20 × sharpe_score
      + 0.10 × trade_count_score
```

Where:
- **return_score** = `tanh(weighted_avg_return × 2)` — dampens extreme outliers
- **win_rate** = fraction of buy trades that are currently profitable
- **sharpe_score** = cross-sectional Ret/Risk ratio, clamped to [0, 3]
- **trade_count_score** = saturates at 30 trades (penalizes very thin samples)

All weights are configurable via environment variables (`SCORE_W_*`).

The entry price for each trade uses the closing price on `pub_date` — not `tx_date`. See the section below on why this matters.

---

## Look-Ahead Bias: The Core Methodological Choice

This is the part most similar projects get wrong.

When a Congress member executes a trade on day **T**, they have 45 days to file a public disclosure. Capitol Trades publishes it once they do — call that day **P** (the `pub_date`). The spread **P − T** averages around 25–30 days in the data and can legally reach 45 days (late filers go further).

**The wrong approach** (common in hobbyist implementations):
> Use `tx_date` as the signal and entry price. This implies you knew about the trade the same day it happened — before the disclosure was filed.

**What this creates:** a look-ahead bias that inflates backtested returns by roughly 3–6 weeks of price movement per trade. In a strong bull market, that can be the difference between a strategy that "works" and one that doesn't.

**This system's approach:**
> All ranking, portfolio construction, and backtest simulation use `pub_date` exclusively. In the walk-forward, point `T` in the backtest only sees trades where `pub_date ≤ T`. The entry price is the closing price on `pub_date`.

From `backtest.py`:
```python
# Point-in-time filter — no future information
pub_dt = datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc)
if pub_dt > as_of or pub_dt < cutoff:
    continue
```

This makes the system more conservative and harder to game with hindsight.

---

## Backtest Results

**Period:** December 2025 – July 2026 (8 monthly periods)  
**Method:** Walk-forward, 180-day ranking window, 30-day steps  
**Benchmark:** SPY (daily closes via yfinance)  
**Initial capital:** $100,000 (simulated)

### Full universe (all eligible members, ≥5 disclosed buys)

| Metric | Bot | SPY |
|--------|-----|-----|
| Total return | **−13.29%** | **+11.44%** |
| vs. Benchmark | **−24.73 pp** | — |
| Annualized return | −19.26% | +17.64% |
| Annualized Sharpe | −0.98 | +0.93 |
| Max drawdown | −18.78% | −4.62% |

**Period-by-period:**

| Date | Top member | Bot monthly | SPY monthly | Portfolio |
|------|------------|-------------|-------------|-----------|
| 2025-12-23 | Byron Donalds (17 trades) | +3.45% | +0.15% | $103,452 |
| 2026-01-22 | Lloyd Doggett (3 trades) | +0.55% | −0.96% | $104,022 |
| 2026-02-21 | Lloyd Doggett (2 trades) | −10.92% | −3.70% | $92,660 |
| 2026-03-23 | Lloyd Doggett (2 trades) | +5.18% | +8.52% | $97,460 |
| 2026-04-22 | John Fetterman (3 trades) | +4.68% | +4.84% | $102,017 |
| 2026-05-22 | Edward Case (8 trades) | −11.29% | +0.09% | $90,501 |
| 2026-06-21 | Edward Case (9 trades) | −6.64% | +0.52% | $84,488 |
| 2026-07-21 | Edward Case (9 trades) | +2.63% | +1.91% | $86,710 |

### Robustness check: excluding hypertraders (>100 disclosed buys per window)

Some members (e.g. Rohit Khanna with 449 buys in 90 days) trade at machine-like frequency, likely via a managed account. Excluding them tests whether the strategy depends on these outliers.

| Metric | Full universe | Without hypertraders |
|--------|--------------|----------------------|
| Total return | −13.29% | −13.29% |
| Sharpe | −0.98 | −0.98 |

**Verdict:** the result is identical. In this period, hypertraders never topped the ranking — the leaders were members with 3–17 trades. The underperformance is not an artifact of outlier behavior.

---

## Limitations

These are not caveats buried in an appendix. They are central to interpreting the results.

**1. Only 8 backtesting periods**

Statistical finance typically requires 36+ observations to make meaningful inferences about a strategy's expected return. With 8 monthly periods, the standard error of the mean return is large enough that we cannot reject the hypothesis that the true expected return is zero — in either direction. The result is directionally informative, not statistically conclusive.

**2. Data only from June 2025**

Capitol Trades's web interface indexes approximately 14 months of disclosures. `txDate=all` returns 3,081 pages, but the `pub_date` of those pages only reaches back to June 2025. Earlier filings are not accessible via scraping. Academic papers studying congressional trading (e.g. Eggers & Hainmueller 2014, Karadas 2019) use proprietary or Freedom of Information Act data going back to 2004. We cannot replicate those findings or refute them with 14 months.

**3. The 180-day ranking window is a compromise**

A 365-day window is standard in the literature but yields 0 usable walk-forward periods with 14 months of data. The 180-day window maximizes periods (8) at the cost of smaller trade samples per member, making scores noisier. The minimum of 5 disclosed buys is a weak filter for statistical reliability.

**4. No transaction costs or market impact**

The simulation trades at closing prices with no slippage, commissions, or spread. For small-cap or illiquid stocks (which appear in congressional portfolios), this can meaningfully overstate returns.

**5. Partial survivorship bias**

Price history is fetched for tickers currently present in the trade cache. Stocks that were delisted, acquired, or went bankrupt before June 2025 are not represented. This biases the price lookup toward survivors, slightly overstating returns. The effect is small over a 14-month window but would compound in a longer backtest.

**6. Score weights are not optimized**

The weights (40/30/20/10) were chosen by judgment, not by cross-validated hyperparameter search. Optimizing weights in-sample and testing out-of-sample is a separate research question this project does not address.

---

## What This Project Demonstrates

**Methodological rigor:**
- Correct treatment of look-ahead bias via `pub_date` gating (most hobbyist projects use `tx_date`)
- Walk-forward simulation with point-in-time data filtering
- Explicit robustness check against outlier members
- Honest reporting of negative results with stated confidence limitations

**Engineering:**
- End-to-end pipeline from raw HTML scraping to order execution
- Resilient scraper with exponential backoff, partial checkpointing, and rate-limit handling
- Persistent state via Git commits from GitHub Actions (no paid infrastructure)
- Modular design: strategy logic decoupled from execution and logging

**What it does not demonstrate:**
- That copying congressional trades generates alpha — the 8-period window is too short to conclude this in either direction
- That the score weights are optimal — they were not validated out-of-sample
- Anything about 2019–2024 — those years are outside the available data

---

## Running It

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (copy .env.example)
cp .env.example .env
# Fill in ALPACA_KEY, ALPACA_SECRET, RESEND_API_KEY, EMAIL_TO

# Build historical trade cache
python fetch_historical.py

# Run one daily cycle (dry run via paper trading)
python main.py

# Run the backtest
python backtest.py
```

**GitHub Actions** runs `main.py` automatically on weekdays at 15:35 UTC. State is committed back to the repository after each run. No paid server required.

---

## Data Sources

| Source | Used for | Notes |
|--------|----------|-------|
| Capitol Trades | Congressional disclosures | Web scraping; ~14 months accessible |
| Alpaca (IEX feed) | Portfolio price history | Free tier; excludes some ETFs |
| yfinance | SPY benchmark | Used for backtest benchmark only |
| Wikipedia | S&P 500 constituent list | For PEAD strategy candidate universe |

---

## References

- Eggers, A. C. & Hainmueller, J. (2014). *Capitol Losses: The Mediocre Performance of Congressional Stock Portfolios.* Journal of Politics.
- Karadas, S. (2019). *Trading on Private Information: Evidence from Members of Congress.* Financial Management.
- Ziobrowski, A. J. et al. (2004). *Abnormal Returns from the Common Stock Investments of the United States Senate.* Journal of Financial and Quantitative Analysis.

These papers use pre-2012 data (before the STOCK Act tightened disclosure requirements) and find statistically significant abnormal returns. Whether the effect persists post-STOCK Act is an open empirical question this project cannot answer with 14 months of data.
