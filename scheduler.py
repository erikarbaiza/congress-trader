"""
Entry point for Railway deployment.
Runs main.run() every weekday at 13:35 UTC (= 09:35 ET / 15:35 Spain summer time).
Keeps the process alive so Railway doesn't kill it.
"""

import time
import datetime
import schedule
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def job():
    # schedule fires daily — skip weekends
    if datetime.datetime.utcnow().weekday() >= 5:
        log.info("Weekend — skipping.")
        return
    log.info("Market open — starting CongressTrader run...")
    try:
        from main import run
        run()
    except Exception as e:
        log.error(f"Run failed: {e}", exc_info=True)


# 13:35 UTC = 09:35 ET (EDT summer) = 15:35 Madrid (CEST summer)
schedule.every().day.at("13:35").do(job)

log.info("Scheduler started. Next run at 13:35 UTC on next weekday.")

while True:
    schedule.run_pending()
    time.sleep(30)
