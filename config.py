import os
from dotenv import load_dotenv

load_dotenv()

# ── Alpaca ────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]

# Paper trading is the default. Live trading requires explicit opt-in.
REAL_TRADING  = os.getenv("REAL_TRADING", "false").lower() == "true"
PAPER_TRADING = not REAL_TRADING

if REAL_TRADING:
    ALPACA_BASE_URL = "https://api.alpaca.markets"
    print("[config] WARNING: REAL TRADING MODE — real money at risk.")
else:
    ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

# ── Email (Resend HTTP API — works on cloud platforms that block SMTP) ────────
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_TO       = os.environ["EMAIL_TO"]

# ── Strategy ──────────────────────────────────────────────────────────────────
DEPLOY_BUDGET    = float(os.getenv("DEPLOY_BUDGET", "0"))
MIN_TRADES       = int(os.getenv("MIN_TRADES", "5"))
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", "8"))
TOP_PERFORMERS   = int(os.getenv("TOP_PERFORMERS", "1"))

# ── Earnings Momentum (PEAD) ──────────────────────────────────────────────────
HOLD_DAYS        = int(os.getenv("HOLD_DAYS",          "10"))   # calendar days to hold
MIN_SURPRISE_PCT = float(os.getenv("MIN_SURPRISE_PCT", "0.05")) # min EPS beat (5%)

# Score weights — must sum to 1.0. Treat as hyperparameters; optimise via backtest.
SCORE_W_RETURN  = float(os.getenv("SCORE_W_RETURN",  "0.40"))
SCORE_W_WINRATE = float(os.getenv("SCORE_W_WINRATE", "0.30"))
SCORE_W_SHARPE  = float(os.getenv("SCORE_W_SHARPE",  "0.20"))
SCORE_W_TRADES  = float(os.getenv("SCORE_W_TRADES",  "0.10"))

# Ranking criterion: composite | best_ret | best_wr
# backtest_comparison.py showed best_ret (+14.90%) beats composite (-12.57%) over 8 periods
RANK_BY = os.getenv("RANK_BY", "best_ret")

# ── Risk controls (YOLO MODE 🚀) ──────────────────────────────────────────────
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))   # 20% por posición
MAX_SECTOR_PCT   = float(os.getenv("MAX_SECTOR_PCT",   "1.00"))   # sin límite sector
MIN_PRICE        = float(os.getenv("MIN_PRICE",        "5.0"))    # no penny stocks
MIN_VOLUME       = int(os.getenv("MIN_VOLUME",         "40000"))  # min avg daily volume
STOP_LOSS_PCT    = float(os.getenv("STOP_LOSS_PCT",    "0.00"))   # sin stop-loss
TAKE_PROFIT_PCT  = float(os.getenv("TAKE_PROFIT_PCT",  "1.00"))   # take-profit al 100% (x2)

CAPITOLTRADES_BFF = "https://bff.capitoltrades.com"
