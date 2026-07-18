"""
Sends the daily summary email via the Resend HTTP API.
Two-section layout: simple summary on top, advanced detail below.
"""

import requests
from datetime import datetime

from config import RESEND_API_KEY, EMAIL_TO, STOP_LOSS_PCT, TAKE_PROFIT_PCT


def _metrics_table(metrics: dict | None, account_equity: float) -> str:
    if not metrics:
        return ""
    p_ret = metrics["portfolio_return"]
    s_ret = metrics["spy_return"]
    diff = p_ret - s_ret
    p_color = "#16a34a" if p_ret >= 0 else "#dc2626"
    s_color = "#16a34a" if s_ret >= 0 else "#dc2626"
    d_color = "#16a34a" if diff >= 0 else "#dc2626"
    d_label = "▲ supera al índice" if diff >= 0 else "▼ por debajo del índice"
    start = metrics.get("initial_equity", 100_000)
    spy_b = metrics.get("spy_baseline", 0)
    spy_now = metrics.get("spy_price", 0)
    return f"""
      <div style="margin-top:20px;border-top:1px solid #e2e8f0;padding-top:16px">
        <h4 style="margin:0 0 8px;font-size:14px">Rendimiento acumulado vs S&P 500</h4>
        <table style="width:100%;border-collapse:collapse;font-size:13px;background:white;border:1px solid #e2e8f0;border-radius:8px">
          <tr style="background:#f1f5f9">
            <th style="padding:8px 12px;text-align:left;color:#64748b">Desde el inicio</th>
            <th style="padding:8px 12px;text-align:right;color:#64748b">Bot (${start:,.0f} → ${account_equity:,.0f})</th>
            <th style="padding:8px 12px;text-align:right;color:#64748b">S&P 500 (SPY ${spy_b:.0f} → ${spy_now:.0f})</th>
            <th style="padding:8px 12px;text-align:right;color:#64748b">Diferencia</th>
          </tr>
          <tr>
            <td style="padding:8px 12px;color:#64748b">{metrics.get('date','')}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:700;color:{p_color}">{p_ret:+.2%}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:700;color:{s_color}">{s_ret:+.2%}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:700;color:{d_color}">{diff:+.2%} &nbsp;<span style="font-weight:400;font-size:11px">{d_label}</span></td>
          </tr>
        </table>
      </div>"""


def send_summary(
    top_performer: dict,
    rankings: list[dict],
    target_positions: list[dict],
    trade_log: list[dict],
    account_equity: float,
    prev_equity: float = 100_000.0,
    metrics: dict | None = None,
) -> None:
    now = datetime.now()
    date_str = now.strftime("%A %d %b %Y").capitalize()
    time_str = now.strftime("%H:%M")

    def pct(v: float) -> str:
        return f"{v * 100:+.2f}%"

    # ── daily P&L ────────────────────────────────────────────────────────────
    daily_change = account_equity - prev_equity
    daily_sign = "+" if daily_change >= 0 else ""
    daily_color = "#16a34a" if daily_change >= 0 else "#dc2626"
    equity_vs_start = account_equity - 100_000
    equity_sign = "+" if equity_vs_start >= 0 else ""
    equity_color = "#16a34a" if equity_vs_start >= 0 else "#dc2626"

    # ── orders in plain language ──────────────────────────────────────────────
    action_icons = {"BUY": "🟢", "SELL": "🔴", "CLOSE": "⛔", "HOLD": "➡️",
                    "SKIP": "⏭️", "STOP_LOSS": "🛑", "TAKE_PROFIT": "💰"}

    def order_label(o: dict) -> str:
        action = o["action"]
        icon = action_icons.get(action, "•")
        ticker = o["ticker"]
        qty = o.get("qty")
        reason = o.get("reason", "")

        if action == "BUY":
            return f"{icon} COMPRADO — <strong>{ticker}</strong> ({qty} acciones) — {reason}"
        elif action in ("SELL", "CLOSE"):
            return f"{icon} VENDIDO — <strong>{ticker}</strong> — {reason}"
        elif action == "HOLD":
            return f"{icon} MANTENIDO — <strong>{ticker}</strong> sin cambios"
        elif action == "SKIP":
            return f"{icon} DESCARTADO — <strong>{ticker}</strong> — {reason}"
        elif action == "STOP_LOSS":
            return f"{icon} STOP-LOSS — <strong>{ticker}</strong> — {reason}"
        elif action == "TAKE_PROFIT":
            return f"{icon} TAKE-PROFIT — <strong>{ticker}</strong> — {reason}"
        return f"{icon} {action} — <strong>{ticker}</strong> — {reason}"

    orders_html = "\n".join(
        f"<li style='margin:6px 0'>{order_label(o)}</li>"
        for o in trade_log
    ) or "<li style='color:#888'>Sin órdenes hoy</li>"

    # ── open positions ────────────────────────────────────────────────────────
    positions_html = "\n".join(
        f"""<tr>
              <td style='padding:8px 12px'><strong>{p['ticker']}</strong></td>
              <td style='padding:8px 12px'>${p.get('scaled_value', p['net_dollars']):,.0f}</td>
              <td style='padding:8px 12px;color:#888'>{p.get('sector','—')}</td>
            </tr>"""
        for p in target_positions
    ) or "<tr><td colspan='3' style='padding:8px 12px;color:#888'>Sin posiciones abiertas</td></tr>"

    # ── stop/take levels for open positions ──────────────────────────────────
    risk_lines = ""
    if target_positions:
        risk_lines = "<p style='font-size:13px;color:#555;margin:4px 0'>"
        risk_lines += " &nbsp;|&nbsp; ".join(
            f"<strong>{p['ticker']}</strong>: stop-loss si cae >{STOP_LOSS_PCT:.0%} &nbsp; take-profit si sube >{TAKE_PROFIT_PCT:.0%}"
            for p in target_positions
        )
        risk_lines += "</p>"

    # ── ranking table ─────────────────────────────────────────────────────────
    ranking_rows = "\n".join(
        f"""<tr style='background:{"#f0fdf4" if i == 0 else "white"}'>
              <td style='padding:6px 10px;text-align:center'>{i+1}</td>
              <td style='padding:6px 10px'>{"⭐ " if i == 0 else ""}{r['pol_name']}</td>
              <td style='padding:6px 10px;text-align:center'><strong>{r.get('score','—')}</strong></td>
              <td style='padding:6px 10px;text-align:right;color:{"#16a34a" if r["return"]>0 else "#dc2626"}'>{pct(r['return'])}</td>
              <td style='padding:6px 10px;text-align:center'>{r.get('win_rate',0):.0%}</td>
              <td style='padding:6px 10px;text-align:center'>{r.get('sharpe',0):.2f}</td>
              <td style='padding:6px 10px;text-align:center'>{r['n_trades']}</td>
            </tr>"""
        for i, r in enumerate(rankings[:10])
    )

    top = top_performer
    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#1a1a1a;max-width:700px;margin:auto;padding:20px">

    <!-- HEADER -->
    <div style="background:#0f172a;color:white;padding:20px 24px;border-radius:10px 10px 0 0">
      <h2 style="margin:0;font-size:20px">CongressTrader</h2>
      <p style="margin:4px 0 0;color:#94a3b8;font-size:14px">{date_str} &nbsp;·&nbsp; {time_str}</p>
    </div>

    <!-- SECTION 1: RESUMEN SIMPLE -->
    <div style="background:#f8fafc;padding:20px 24px;border:1px solid #e2e8f0">

      <h3 style="margin:0 0 16px;font-size:16px;color:#475569;text-transform:uppercase;letter-spacing:.05em">Resumen del día</h3>

      <!-- Portfolio -->
      <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:20px">
        <div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:14px 20px;flex:1;min-width:140px">
          <p style="margin:0;font-size:12px;color:#94a3b8;text-transform:uppercase">Portfolio</p>
          <p style="margin:4px 0 0;font-size:24px;font-weight:700">${account_equity:,.2f}</p>
          <p style="margin:4px 0 0;font-size:13px;color:{equity_color}">{equity_sign}${equity_vs_start:,.2f} desde el inicio</p>
        </div>
        <div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:14px 20px;flex:1;min-width:140px">
          <p style="margin:0;font-size:12px;color:#94a3b8;text-transform:uppercase">Cambio hoy</p>
          <p style="margin:4px 0 0;font-size:24px;font-weight:700;color:{daily_color}">{daily_sign}${daily_change:,.2f}</p>
          <p style="margin:4px 0 0;font-size:13px;color:#94a3b8">{daily_sign}{abs(daily_change)/prev_equity*100:.2f}%</p>
        </div>
        <div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:14px 20px;flex:1;min-width:140px">
          <p style="margin:0;font-size:12px;color:#94a3b8;text-transform:uppercase">Siguiendo a</p>
          <p style="margin:4px 0 0;font-size:16px;font-weight:700">{top['pol_name']}</p>
          <p style="margin:4px 0 0;font-size:13px;color:#16a34a">{pct(top['return'])} en 12 meses</p>
        </div>
      </div>

      <!-- Posiciones -->
      <h4 style="margin:0 0 8px;font-size:14px">Posiciones abiertas ({len(target_positions)})</h4>
      <table style="width:100%;border-collapse:collapse;background:white;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:16px">
        <tr style="background:#f1f5f9">
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#64748b">Ticker</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#64748b">Asignación</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#64748b">Sector</th>
        </tr>
        {positions_html}
      </table>
      {risk_lines}

      <!-- Órdenes -->
      <h4 style="margin:16px 0 8px;font-size:14px">Órdenes de hoy</h4>
      <ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.6">
        {orders_html}
      </ul>

      {_metrics_table(metrics, account_equity)}
    </div>

    <!-- SECTION 2: DETALLE AVANZADO -->
    <div style="background:white;padding:20px 24px;border:1px solid #e2e8f0;border-top:none">

      <h3 style="margin:0 0 4px;font-size:16px;color:#475569;text-transform:uppercase;letter-spacing:.05em">Detalle avanzado</h3>
      <p style="margin:0 0 16px;font-size:12px;color:#94a3b8">Ranking completo · Score = 40% retorno + 30% win rate + 20% Sharpe + 10% nº trades</p>

      <!-- Top performer stats -->
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px">
        <strong>⭐ {top['pol_name']}</strong> &nbsp;·&nbsp;
        Score: <strong>{top.get('score','—')}/100</strong> &nbsp;·&nbsp;
        Retorno 12M: <strong style="color:#16a34a">{pct(top['return'])}</strong> &nbsp;·&nbsp;
        Win rate: <strong>{top.get('win_rate',0):.0%}</strong> &nbsp;·&nbsp;
        Sharpe: <strong>{top.get('sharpe',0):.2f}</strong> &nbsp;·&nbsp;
        Trades analizados: <strong>{top['n_trades']}</strong>
      </div>

      <!-- Ranking table -->
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f1f5f9">
          <th style="padding:6px 10px;text-align:center">#</th>
          <th style="padding:6px 10px;text-align:left">Congresista</th>
          <th style="padding:6px 10px;text-align:center">Score</th>
          <th style="padding:6px 10px;text-align:right">Retorno 12M</th>
          <th style="padding:6px 10px;text-align:center">Win Rate</th>
          <th style="padding:6px 10px;text-align:center">Sharpe</th>
          <th style="padding:6px 10px;text-align:center">Trades</th>
        </tr>
        {ranking_rows}
      </table>

      <p style="margin:20px 0 0;font-size:11px;color:#94a3b8;border-top:1px solid #f1f5f9;padding-top:12px">
        Paper trading — dinero virtual. Ninguna cantidad real está en riesgo.
        Stop-loss: -{STOP_LOSS_PCT:.0%} &nbsp;·&nbsp; Take-profit: +{TAKE_PROFIT_PCT:.0%} &nbsp;·&nbsp; Datos: Capitol Trades + Alpaca IEX
      </p>
    </div>

    </body></html>
    """

    subject = f"CongressTrader {date_str} — {top['pol_name']} {pct(top['return'])} | ${account_equity:,.0f}"

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": "CongressTrader <onboarding@resend.dev>",
            "to": [EMAIL_TO],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    resp.raise_for_status()

    print(f"[emailer] Summary sent to {EMAIL_TO}")
