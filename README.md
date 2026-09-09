# CongressTrader

A quantitative system that ranks U.S. Congress members by historical stock performance and automatically replicates the top-ranked portfolio in Alpaca paper trading. Includes a walk-forward backtest with deliberate look-ahead bias correction.

> **Honest upfront:** the 8-period backtest shows the bot returning **−12.6%** while SPY returned **+11.4%** (−24 pp gap). A composite scoring formula ranked **below random selection** by 21 pp. The lag of public disclosures adds no alpha. Transaction costs amplify but don't explain the loss. This README documents a rigorous negative result.

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

### Full universe (all eligible members, ≥5 disclosed buys, `TOP_PERFORMERS=2`)

| Metric | Bot | SPY |
|--------|-----|-----|
| Total return | **−12.57%** | **+11.44%** |
| vs. Benchmark | **−24.01 pp** | — |
| Annualized return | −18.25% | +17.64% |
| Annualized Sharpe | −0.99 | +0.93 |
| Max drawdown | −14.00% | −4.62% |

**Period-by-period:**

| Date | Top member | Bot monthly | SPY monthly | Portfolio |
|------|------------|-------------|-------------|-----------|
| 2025-12-23 | Byron Donalds (16 trades) | −1.55% | +0.15% | $98,450 |
| 2026-01-22 | Julie Johnson (8 trades) | −1.87% | −0.96% | $96,611 |
| 2026-02-21 | Timothy Moore (33 trades) | −7.88% | −3.70% | $88,998 |
| 2026-03-23 | Jared Moskowitz (20 trades) | +7.88% | +8.52% | $96,009 |
| 2026-04-22 | Jared Moskowitz (20 trades) | +5.88% | +4.84% | $101,659 |
| 2026-05-22 | David McCormick (7 trades) | −12.55% | +0.09% | $88,901 |
| 2026-06-21 | David McCormick (7 trades) | −1.13% | +0.52% | $87,900 |
| 2026-07-21 | David McCormick (9 trades) | −0.54% | +1.91% | $87,427 |

### Robustness check: excluding hypertraders (>100 disclosed buys per window)

Some members (e.g. Rohit Khanna with 449 buys in 90 days) trade at machine-like frequency, likely via a managed account. Excluding them tests whether the strategy depends on these outliers.

| Metric | Full universe | Without hypertraders |
|--------|--------------|----------------------|
| Total return | −12.57% | −12.57% |
| Sharpe | −0.99 | −0.99 |

**Verdict:** identical. In this period, hypertraders never topped the ranking — the leaders were members with 7–33 trades. The underperformance is not an artifact of outlier behavior.

---

## Investigación adicional / Extended Analysis

Las tres secciones siguientes amplían el análisis base con tests de robustez diseñados para cuestionar el resultado, no para validarlo. Los resultados son descriptivos — 8 periodos no dan significancia estadística — pero la consistencia del hallazgo negativo en todos los ángulos es informativa.

### Mejora 1: Comparación contra baselines

**Pregunta:** ¿el scoring compuesto añade valor sobre estrategias simples o sobre el azar?

El mismo walk-forward (180d ranking, 30d step, `TOP_PERFORMERS=2`) se ejecutó con cinco estrategias de selección distintas. "Aleatorio" promedia 30 semillas para estabilizar la estimación:

| Estrategia | Retorno total | vs SPY | Sharpe | Max DD |
|------------|--------------|--------|--------|--------|
| **Score compuesto ★** | **−12.57%** | **−24.01 pp** | **−0.99** | **−14.00%** |
| Mayor retorno | +14.90% | +3.46 pp | +0.80 | −8.16% |
| Mayor win rate | −8.16% | −19.60 pp | −0.68 | −13.88% |
| Aleatorio (30 seeds) | +8.99% | −2.45 pp | +0.76 | −6.06% |
| SPY buy & hold | +11.44% | — | +0.93 | −4.62% |

**Hallazgo clave:** el scoring compuesto queda último, por debajo incluso del aleatorio en 21.56 pp y con el peor Sharpe de la tabla. La fórmula no captura señal predictiva: en estos 8 periodos, ignorar el score y elegir al azar habría funcionado mejor. Seleccionar por mayor retorno pasado generó el único resultado positivo — pero ese ranking trivial también tiene sesgo de recencia y sería esperable que revierta.

Esto no implica que el composite sea inútil en general: con 8 periodos no hay potencia para distinguir habilidad de suerte. Pero el resultado invierte la carga de la prueba: antes de asumir que un scoring sofisticado ayuda, habría que verlo ganar al azar de forma consistente en periodos más largos.

---

### Mejora 2: Análisis del lag de publicación

**Hipótesis:** las operaciones publicadas rápido (lag tx_date → pub_date corto) deberían tener más alpha porque el mercado no ha tenido tiempo de incorporar la señal.

Metodología: todas las compras con ambas fechas disponibles, agrupadas por lag, retorno medido desde pub_date a +30d y +60d. Alpha = retorno del trade − retorno de SPY en el mismo horizonte.

**Distribución del lag:** rango 1–876 días, media ~49.6d, mediana ~28d.

**Retorno a 30 días por bucket:**

| Bucket | N operaciones | N con precio | Ret medio | Alpha medio | % positivo |
|--------|-------------|-------------|-----------|-------------|-----------|
| 0-7d (urgente) | ~1,100 | ~450 | +4–6% | ~0% | ~52% |
| 8-14d (rápido) | ~2,200 | ~900 | +3–5% | ~0% | ~51% |
| 15-30d (normal) | ~4,500 | ~1,800 | +3–5% | ~0% | ~51% |
| 31-45d (lento) | ~3,500 | ~1,400 | +3–5% | ~0% | ~51% |
| 46+d (tardío) ⚠️ | ~2,000 | ~800 | +3–6% | ~0% | ~52% |
| **TOTAL** | **~13,300** | **~5,350** | **~+4%** | **~−0.17%** | **~51%** |

*(El retorno bruto de ~+4% refleja el mercado alcista del período, no alpha: SPY subió ~11% en el mismo horizonte.)*

**Alpha 30d entre extremos:** lag 0-7d vs lag 46+d → diferencia < 2 pp. No hay gradiente monotónico.

**A 60 días:** alpha medio total −0.58%, igualmente plano entre grupos.

**Hallazgo:** la velocidad de publicación no es alpha explotable. El retorno medio de copiar cualquier operación de congresista en el periodo es aproximadamente cero una vez descontado el mercado, independientemente de cuánto tarde en publicarse. El bucket 46+d está además contaminado: lags de hasta 876 días exceden el límite legal del STOCK Act (45 días), lo que apunta a errores de datos o filings tardíos muy excepcionales — ese grupo no representa una estrategia ejecutable.

**Nota estadística:** con std intra-grupo de ~20-40% y grupos de 450-1,800 observaciones, el error estándar de la media por grupo está entre 1-2 pp. Diferencias menores de 3-4 pp son ruido. La variable omitida clave: el lag correlaciona con el perfil del político (quienes más operan declaran más rápido), no necesariamente con la calidad de la señal.

---

### Mejora 3: Impacto de costes de transacción

**El backtest asume ejecución perfecta sin costes.** Esta mejora cuantifica cuánto se comería la fricción real en tres escenarios.

Metodología: costes aplicados solo a entradas nuevas y salidas reales (posiciones que continúan del mes anterior no pagan). Cada dirección paga `each_way = rt/2`. El turnover observado fue del **96% mensual** (la mayoría de posiciones rotan cada mes).

| Escenario | Modelo | Coste RT | Arrastre total | Retorno neto | vs sin costes |
|-----------|--------|---------|---------------|-------------|---------------|
| Optimista | Sin comisión, spread ~1bp, slippage ~4bp | 10 bp RT | −0.69 pp | **−13.27%** | −0.69 pp |
| Normal | Sin comisión, spread ~5bp, slippage ~10bp | 30 bp RT | −2.07 pp | **−14.64%** | −2.07 pp |
| Pesimista | Comisión 5bp, spread ~15bp, slippage ~30bp | 100 bp RT | −6.74 pp | **−19.31%** | −6.74 pp |

**Hallazgo:** los costes amplifican las pérdidas pero no las explican. Con broker sin comisión y activos líquidos (escenario optimista), el arrastre es mínimo (−0.69 pp). El problema de selección — elegir políticos que no baten al mercado — domina sobre la fricción.

El turnover del 96% mensual sí sería un factor relevante en producción con activos ilíquidos o con slippage real. En el escenario pesimista (activos menos líquidos, órdenes de tamaño medio), los costes se comerían casi 7 puntos adicionales sobre un resultado ya negativo.

---

These are not caveats buried in an appendix. They are central to interpreting the results.

**1. Only 8 backtesting periods**

Statistical finance typically requires 36+ observations to make meaningful inferences about a strategy's expected return. With 8 monthly periods, the standard error of the mean return is large enough that we cannot reject the hypothesis that the true expected return is zero — in either direction. The result is directionally informative, not statistically conclusive.

**2. Data only from June 2025**

Capitol Trades's web interface indexes approximately 14 months of disclosures. `txDate=all` returns 3,081 pages, but the `pub_date` of those pages only reaches back to June 2025. Earlier filings are not accessible via scraping. Academic papers studying congressional trading (e.g. Eggers & Hainmueller 2014, Karadas 2019) use proprietary or Freedom of Information Act data going back to 2004. We cannot replicate those findings or refute them with 14 months.

**3. The 180-day ranking window is a compromise**

A 365-day window is standard in the literature but yields 0 usable walk-forward periods with 14 months of data. The 180-day window maximizes periods (8) at the cost of smaller trade samples per member, making scores noisier. The minimum of 5 disclosed buys is a weak filter for statistical reliability.

**4. Transaction costs are modeled but not the primary issue**

The base backtest trades at closing prices with no friction. The Extended Analysis (Mejora 3) quantifies the impact: with a no-commission broker and liquid stocks (optimistic scenario, 10 bp round-trip), cost drag is only −0.69 pp over the full period. Even in a pessimistic scenario (100 bp RT), costs add −6.74 pp but the strategy is already deeply negative before them. The problem is selection, not friction — though the 96% monthly turnover would matter meaningfully in production with less liquid names.

**5. Partial survivorship bias**

Price history is fetched for tickers currently present in the trade cache. Stocks that were delisted, acquired, or went bankrupt before June 2025 are not represented. This biases the price lookup toward survivors, slightly overstating returns. The effect is small over a 14-month window but would compound in a longer backtest.

**6. Score weights are not optimized**

The weights (40/30/20/10) were chosen by judgment, not by cross-validated hyperparameter search. Optimizing weights in-sample and testing out-of-sample is a separate research question this project does not address.

---

## Conclusión / What This Project Demonstrates

**Conclusión principal:** copiar las operaciones del Congreso estadounidense **no genera alpha explotable** en el período estudiado (diciembre 2025 – julio 2026). El resultado negativo es consistente desde todos los ángulos analizados:

- El bot devuelve −12.57% frente a +11.44% de SPY (−24 pp de diferencia).
- Un scoring compuesto elaborado (40% retorno + 30% win rate + 20% Sharpe + 10% nTrades) queda por debajo de la selección aleatoria en 21 pp — la fórmula no captura señal predictiva.
- La velocidad de publicación (lag tx→pub) no tiene relación con el alpha posterior: publicaciones urgentes (0-7d) y tardías (46+d) dan resultados equivalentes (~0% de alpha sobre SPY).
- Los costes de transacción amplifican el resultado pero no lo explican: con un broker sin comisión y activos líquidos, el arrastre es solo −0.69 pp.

**El valor del proyecto es el resultado negativo bien documentado.** La mayoría de implementaciones de este tipo usan `tx_date` en vez de `pub_date`, introduciendo un sesgo de look-ahead que puede inflar los retornos 3-6 semanas artificialmente. Este proyecto usa `pub_date` en toda la pipeline — scraping, scoring, backtest y análisis extendido — lo que lo hace más conservador y más honesto.

**Limitaciones del alcance:** 14 meses de datos, 8 periodos de backtest. Con este volumen no es posible rechazar estadísticamente la hipótesis nula en ninguna dirección. Los papers académicos (Ziobrowski et al. 2004, Eggers & Hainmueller 2014) usan datos de 2004-2010, anteriores al STOCK Act; si ese efecto existió, puede que se haya arbitrado desde entonces. Esta pregunta no se puede responder con 14 meses.

---

**Methodological rigor demonstrated:**
- Correct treatment of look-ahead bias via `pub_date` gating (most hobbyist projects use `tx_date`)
- Walk-forward simulation with point-in-time data filtering
- Baseline comparisons (vs. random, simple heuristics, buy-and-hold)
- Lag analysis isolating the publication-delay dimension of the hypothesis
- Transaction cost sensitivity across three scenarios with carry-based turnover tracking
- Consistent price data via shared disk cache (eliminates IEX non-determinism across scripts)
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
