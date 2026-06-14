"""
Sends the daily summary email via Gmail SMTP using an App Password.
Setup: myaccount.google.com/apppasswords → create a password for "Mail".
"""

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO


def send_summary(
    top_performer: dict,
    rankings: list[dict],
    target_positions: list[dict],
    trade_log: list[dict],
    account_equity: float,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── build HTML ──────────────────────────────────────────────────────────
    def pct(v: float) -> str:
        return f"{v * 100:+.2f}%"

    ranking_rows = "\n".join(
        f"<tr><td>{i+1}</td><td>{r['pol_name']}</td>"
        f"<td><strong>{r.get('score','—')}</strong></td>"
        f"<td>{pct(r['return'])}</td>"
        f"<td>{r.get('win_rate',0):.0%}</td>"
        f"<td>{r.get('sharpe',0):.2f}</td>"
        f"<td>{r['n_trades']}</td></tr>"
        for i, r in enumerate(rankings[:10])
    )

    position_rows = "\n".join(
        f"<tr><td>{p['ticker']}</td>"
        f"<td>${p['net_dollars']:,.0f}</td></tr>"
        for p in target_positions
    )

    order_rows = "\n".join(
        f"<tr><td>{o['action']}</td><td>{o['ticker']}</td>"
        f"<td>{o['qty'] or '—'}</td><td>{o['reason']}</td></tr>"
        for o in trade_log
    )

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
    <h2>CongressTrader — Daily Report {now}</h2>
    <p><strong>Account equity:</strong> ${account_equity:,.2f}</p>

    <h3>Top Performer</h3>
    <p><strong>{top_performer['pol_name']}</strong> &nbsp;|&nbsp;
    Score: <strong>{top_performer.get('score', '—')}/100</strong> &nbsp;|&nbsp;
    12M return: <strong>{pct(top_performer['return'])}</strong> &nbsp;|&nbsp;
    Win rate: {top_performer.get('win_rate', 0):.0%} &nbsp;|&nbsp;
    Sharpe: {top_performer.get('sharpe', 0):.2f} &nbsp;|&nbsp;
    Trades: {top_performer['n_trades']}</p>

    <h3>Congress Member Rankings (top 10)</h3>
    <table border="1" cellpadding="4" cellspacing="0">
    <tr><th>#</th><th>Name</th><th>Score</th><th>12M Return</th><th>Win Rate</th><th>Sharpe</th><th>Trades</th></tr>
    {ranking_rows}
    </table>

    <h3>Mirrored Positions ({len(target_positions)} stocks)</h3>
    <table border="1" cellpadding="4" cellspacing="0">
    <tr><th>Ticker</th><th>Est. Notional</th></tr>
    {position_rows}
    </table>

    <h3>Orders Executed Today ({len(trade_log)})</h3>
    <table border="1" cellpadding="4" cellspacing="0">
    <tr><th>Action</th><th>Ticker</th><th>Qty</th><th>Reason</th></tr>
    {order_rows}
    </table>

    <p style="color:#888;font-size:12px">This is a paper-trading simulation. No real money is at risk.</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"CongressTrader Report — {top_performer['pol_name']} | {now}"
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())

    print(f"[emailer] Summary sent to {EMAIL_TO}")
